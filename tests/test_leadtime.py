"""Verify durable issue/merge joins, missing data and counter-history protection."""
import hashlib
import hmac
import json
import sqlite3
import unittest

from telemetry.events import EventStore
from telemetry.linear import receive_issue
from telemetry.metrics import render_metrics
from telemetry.sources import pull_request, timestamp_ns


class LeadTimeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        self.sequence = 0

    def create(self, issue='KEB-131', created=0, repo='owner/app'):
        self.sequence += 1
        self.store.append('linear', 'issue-' + str(self.sequence), repo, issue, 'in_progress',
                          created, {'issue_created_ns': created})

    def merge(self, pr, seconds, issue='KEB-131', repo='owner/app'):
        self.sequence += 1
        attrs = {'pr': pr, 'sha': 'a' * 40}
        if seconds is not None:
            attrs['merged_ns'] = seconds * 1_000_000_000
        self.store.append('github', 'merge-' + str(self.sequence), repo, issue, 'merged',
                          (seconds or 1) * 1_000_000_000, attrs)

    def metrics(self, now=1_000_000):
        return render_metrics(self.db, ['owner/app'], now_ns=now * 1_000_000_000)

    def test_normalizers_preserve_entity_creation_and_merge_not_delivery_times(self):
        secret = b'fixture-only-' * 4
        payload = {'organizationId': 'fixture', 'webhookTimestamp': 1000, 'type': 'Issue',
                   'action': 'update', 'createdAt': '2026-11-01T12:10:00Z', 'data': {
                       'id': 'issue', 'stateId': 'done', 'createdAt': '2026-11-01T12:00:00Z',
                       'updatedAt': '2026-11-01T12:10:00Z', 'title': 'private-fixture'}}
        body = json.dumps(payload).encode()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        self.assertTrue(receive_issue(self.store, body, signature, secret, 'fixture',
                                     {'issue': ('owner/app', 'KEB-131')}, {'done': 'done'}, 1000))
        pull_request(self.store, {'repository': {'full_name': 'owner/app'}, 'action': 'closed',
                     'pull_request': {'number': 1, 'merged': True, 'state': 'closed',
                                      'head': {'sha': 'a' * 40}, 'merged_at': '2026-11-01T12:05:00Z',
                                      'updated_at': '2026-11-01T12:15:00Z', 'body': 'private-fixture'}},
                     'KEB-131', 'delivery')
        text = self.metrics(now=timestamp_ns('2026-11-01T13:00:00Z') // 1_000_000_000)
        self.assertIn('agentic_issue_to_merge_seconds_sum{repository="owner/app"} 300\n', text)
        self.assertNotIn('private-fixture', '\n'.join(self.db.iterdump()))

    def test_zero_baseline_first_merge_dedup_scope_and_acknowledgement(self):
        self.create()
        self.assertIn('agentic_issue_to_merge_seconds_count{repository="owner/app"} 0\n', self.metrics())
        self.merge(1, 300)
        self.merge(1, 300)
        self.merge(2, 500)
        self.create('KEB-132', repo='owner/other')
        self.merge(1, 10, 'KEB-132', 'owner/other')
        text = self.metrics()
        self.assertIn('agentic_issue_to_merge_seconds_bucket{repository="owner/app",le="60"} 0\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_bucket{repository="owner/app",le="300"} 1\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_bucket{repository="owner/app",le="+Inf"} 1\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_count{repository="owner/app"} 1\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_sum{repository="owner/app"} 300\n', text)
        self.assertNotIn('owner/other', text)
        self.store.acknowledge('github', 'merge-2')
        self.assertEqual(self.metrics(), text)

    def test_merge_before_creation_delivery_joins_earliest_known_pr(self):
        self.merge(2, 500)
        self.merge(1, 300)
        text = self.metrics()
        self.assertNotIn('agentic_issue_to_merge_seconds_count{', text)
        self.assertIn('reason="missing_issue_creation"} 1\n', text)
        self.create()
        self.assertIn('agentic_issue_to_merge_seconds_sum{repository="owner/app"} 300\n', self.metrics())

    def test_missing_merge_time_invalid_timing_and_legacy_receipts_are_explicit(self):
        self.create('KEB-131')
        self.merge(1, None)
        self.create('KEB-132', created=500 * 1_000_000_000)
        self.merge(2, 300, 'KEB-132')
        self.create('KEB-133')
        self.merge(3, 400, 'KEB-133')
        self.db.execute("DELETE FROM engineering_receipts WHERE event_id='merge-6'")
        self.db.commit()
        text = self.metrics()
        for reason in ('missing_merge_time', 'invalid_timing', 'legacy_receipt'):
            self.assertIn('reason="' + reason + '"} 1\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_count{repository="owner/app"} 0\n', text)

    def test_late_earlier_merge_cannot_rewrite_existing_counter_history(self):
        self.create()
        self.merge(2, 500)
        self.assertIn('seconds_sum{repository="owner/app"} 500\n', self.metrics())
        self.merge(1, 300)
        with self.assertRaisesRegex(ValueError, 'Historical first merge changed'):
            self.metrics()

    def test_late_incomplete_merge_cannot_remove_existing_sample(self):
        self.create()
        self.merge(1, 300)
        self.merge(2, None)
        with self.assertRaisesRegex(ValueError, 'coverage changed'):
            self.metrics()

    def test_conflicting_merge_times_require_reconciliation(self):
        self.create()
        self.merge(1, 300)
        self.merge(1, 400)
        with self.assertRaisesRegex(ValueError, 'Conflicting associated merge'):
            self.metrics()

    def test_conflicting_issue_creation_requires_reconciliation(self):
        self.create()
        self.merge(1, 300)
        self.create(created=1)
        with self.assertRaisesRegex(ValueError, 'Conflicting issue creation'):
            self.metrics()

    def test_same_pr_cannot_count_for_two_issues(self):
        self.create()
        self.create('KEB-132')
        self.merge(1, 300)
        self.merge(1, 300, 'KEB-132')
        with self.assertRaisesRegex(ValueError, 'Conflicting associated merge'):
            self.metrics()

    def test_future_merge_is_not_a_completed_observation(self):
        self.create()
        self.merge(1, 300)
        text = self.metrics(now=200)
        self.assertIn('reason="invalid_timing"} 1\n', text)
        self.assertIn('agentic_issue_to_merge_seconds_count{repository="owner/app"} 0\n', text)

    def test_timing_metadata_is_source_scoped_and_chronologically_valid(self):
        for source, kind, observed, attrs in (
                ('agent', 'success', 10, {'issue_created_ns': 1}),
                ('linear', 'done', 10, {'issue_created_ns': 11}),
                ('linear', 'done', 10, {'issue_created_ns': None}),
                ('ci', 'success', 10, {'merged_ns': 1, 'pr': 1}),
                ('github', 'opened', 10, {'merged_ns': 1, 'pr': 1}),
                ('github', 'merged', 10, {'merged_ns': 11, 'pr': 1}),
                ('github', 'merged', 10, {'merged_ns': 1})):
            with self.subTest(source=source, kind=kind, attrs=attrs), self.assertRaises(ValueError):
                self.store.append(source, 'invalid', 'owner/app', 'KEB-131', kind, observed, attrs)
