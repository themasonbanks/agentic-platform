"""Verify source normalization excludes free text and preserves unknown semantics."""
import sqlite3
import unittest
from telemetry.events import EventStore
from telemetry.sources import workflow_run, pull_request, codex_turn


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)

    def test_workflow_retry_skipped_is_not_success(self):
        workflow_run(self.store, {'repository': {'full_name': 'owner/app'}, 'workflow_run': {
            'id': 1, 'run_attempt': 2, 'head_sha': 'a'*40, 'status': 'completed',
            'conclusion': 'skipped', 'updated_at': '2026-11-01T12:00:00Z',
            'display_title': 'private-value'}}, 'KEB-130')
        event = self.store.pending()[0]
        self.assertEqual(event['kind'], 'skipped')
        self.assertEqual(event['attributes']['retry_count'], 1)
        self.assertNotIn('private-value', str(event))

    def test_pr_lifecycle_does_not_store_title_body_or_user(self):
        pull_request(self.store, {'repository': {'full_name': 'owner/app'}, 'action': 'closed',
                     'pull_request': {'number': 2, 'merged': True, 'state': 'closed',
                     'head': {'sha': 'a'*40}, 'updated_at': '2026-11-01T12:00:00Z',
                     'title': 'private-value', 'body': 'private-value'}}, 'KEB-130', 'delivery-1')
        self.assertEqual(self.store.pending()[0]['kind'], 'merged')
        self.assertNotIn('private-value', str(self.store.pending()))

    def test_codex_usage_only_and_nonterminal_events_ignored(self):
        args = ('event-1', 'owner/app', 'KEB-130', 'run-1', 'a'*40, 1000000000)
        self.assertFalse(codex_turn(self.store, {'type': 'item.completed', 'text': 'private-value'}, *args))
        codex_turn(self.store, {'type': 'turn.completed', 'usage': {'input_tokens': 10,
                   'output_tokens': 2}, 'prompt': 'private-value'}, *args)
        event = self.store.pending()[0]
        self.assertTrue(event['usage_known'])
        self.assertFalse(event['cost_known'])
        self.assertNotIn('private-value', str(event))
