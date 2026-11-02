"""Verify signed review evidence, ambiguity, replay and bounded metric attribution."""
from copy import deepcopy
import hashlib
import hmac
import json
import sqlite3
import unittest

from telemetry.events import EventStore
from telemetry.github import receive
from telemetry.mapping import IssueResolver
from telemetry.metrics import render_metrics
from telemetry.reviews import InlineReviewEvidence


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        self.secret = b'fixture-review-secret-' * 3
        self.resolver = IssueResolver({('owner/app', 3): 'KEB-130'}, {})
        self.calls = []
        self.comments = []
        self.evidence = InlineReviewEvidence(self.api)
        self.payload = {'action': 'submitted', 'repository': {'full_name': 'owner/app'},
                        'installation': {'id': 42}, 'pull_request': {'number': 3, 'head': {'sha': 'a' * 40}},
                        'review': {'id': 17, 'state': 'commented', 'commit_id': 'b' * 40,
                                   'submitted_at': '2026-11-01T12:00:00Z',
                                   'body': 'PRIVATE-review-text', 'user': {'login': 'PRIVATE-person'}}}

    def api(self, path):
        self.calls.append(path)
        return self.comments

    def delivery(self, signature=None, evidence=True):
        body = json.dumps(self.payload).encode()
        signature = signature or 'sha256=' + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return receive(self.store, 'pull_request_review', body, signature, self.secret,
                       {'owner/app': 42}, self.resolver, self.evidence if evidence else None)

    def test_decisions_count_without_inspecting_text_or_calling_comments_api(self):
        for index, state in enumerate(('approved', 'changes_requested')):
            self.payload['review'].update(id=17 + index, state=state)
            self.assertTrue(self.delivery())
        self.assertEqual(self.calls, [])
        text = render_metrics(self.db, ['owner/app'])
        self.assertIn('agentic_review_cycles_total{repository="owner/app"} 2\n', text)
        self.assertIn('agentic_review_unclassified_total{repository="owner/app"} 0\n', text)
        self.assertNotIn('PRIVATE-', '\n'.join(self.db.iterdump()))

    def test_commented_review_requires_bound_inline_evidence_and_replay_does_not_refetch(self):
        self.comments = [{'id': 91, 'pull_request_review_id': 17, 'path': 'src/example.py',
                          'position': 5, 'body': 'PRIVATE-inline-text'}]
        self.assertTrue(self.delivery())
        self.comments = []
        self.assertFalse(self.delivery())
        self.assertEqual(self.calls, ['/repos/owner/app/pulls/3/reviews/17/comments?per_page=1'])
        self.assertIn('agentic_review_cycles_total{repository="owner/app"} 1\n', render_metrics(self.db, ['owner/app']))
        self.assertNotIn('PRIVATE-', '\n'.join(self.db.iterdump()))

    def test_rate_notices_and_comment_only_or_unavailable_evidence_are_not_cycles(self):
        for index, text in enumerate(('Review limit reached', '@coderabbitai review', 'A comment-only summary')):
            self.payload['review'].update(id=17 + index, body=text)
            self.assertTrue(self.delivery(evidence=index != 2))
        metrics = render_metrics(self.db, ['owner/app'])
        self.assertNotIn('agentic_review_cycles_total{', metrics)
        self.assertIn('agentic_review_unclassified_total{repository="owner/app"} 3\n', metrics)
        self.assertNotIn('Review limit reached', '\n'.join(self.db.iterdump()))

    def test_bad_signature_installation_and_mapping_do_not_call_evidence(self):
        with self.assertRaises(ValueError):
            self.delivery(signature='sha256=bad')
        self.payload['installation']['id'] = 43
        with self.assertRaises(ValueError):
            self.delivery()
        self.payload['installation']['id'] = 42
        self.payload['pull_request']['number'] = 4
        with self.assertRaises(ValueError):
            self.delivery()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.pending(), [])

    def test_edited_dismissed_pending_and_malformed_submissions_do_not_count(self):
        for action in ('edited', 'dismissed'):
            self.payload['action'] = action
            self.assertFalse(self.delivery())
        self.payload['action'] = 'submitted'
        original = deepcopy(self.payload['review'])
        for changes in ({'state': 'pending'}, {'id': True}, {'id': 0}, {'commit_id': 'main'}, {'submitted_at': None}):
            self.payload['review'] = dict(original, **changes)
            with self.assertRaises((ValueError, AttributeError)):
                self.delivery()
        self.assertEqual(self.store.pending(), [])

    def test_cross_review_or_malformed_api_evidence_fails_without_ingestion(self):
        good = {'id': 91, 'pull_request_review_id': 17, 'path': 'src/example.py', 'line': 4}
        for comments in ({}, [good, good], [dict(good, pull_request_review_id=18)],
                         [dict(good, id=True)], [dict(good, path=[])], [dict(good, path='')], ['invalid']):
            self.comments = comments
            with self.assertRaises(ValueError):
                self.delivery()
        self.assertEqual(self.store.pending(), [])

    def test_logical_duplicates_do_not_inflate_cycles_and_conflicts_fail(self):
        self.payload['review']['state'] = 'approved'
        self.delivery()
        event = self.store.pending()[0]
        self.store.append('github', 'alternate-id', 'owner/app', 'KEB-130', 'reviewed',
                          event['observed_ns'], event['attributes'])
        self.assertIn('agentic_review_cycles_total{repository="owner/app"} 1\n', render_metrics(self.db, ['owner/app']))
        self.store.append('github', 'conflict', 'owner/app', 'KEB-130', 'reviewed',
                          event['observed_ns'], dict(event['attributes'], sha='c' * 40))
        with self.assertRaises(ValueError):
            render_metrics(self.db, ['owner/app'])
        self.assertNotIn('agentic_review_cycles_total{', render_metrics(self.db, ['owner/other']))

    def test_evidence_scope_validation_precedes_api(self):
        for repository, pr, review in [('owner/app/extra', 3, 17), ('owner/app', True, 17), ('owner/app', 3, -1)]:
            with self.assertRaises(ValueError):
                self.evidence(repository, pr, review)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
