"""Verify GitHub telemetry signatures, installation scope and replay deduplication."""
import hashlib
import hmac
import json
import sqlite3
import unittest
from telemetry.events import EventStore
from telemetry.github import receive


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        self.secret = b'local-github-fixture-secret-' * 2
        self.payload = {'repository': {'full_name': 'owner/app'}, 'installation': {'id': 42},
                        'action': 'opened', 'pull_request': {'number': 3, 'merged': False,
                        'state': 'open', 'updated_at': '2026-11-01T12:00:00Z',
                        'head': {'sha': 'a'*40}, 'body': 'private-value'}}

    def receive(self, signature=None, issue='KEB-130'):
        body = json.dumps(self.payload).encode()
        signature = signature or 'sha256=' + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return receive(self.store, 'pull_request', body, signature, self.secret,
                       {'owner/app': 42}, lambda *args: issue)

    def test_signed_pr_and_replay(self):
        self.assertTrue(self.receive())
        self.assertFalse(self.receive())
        self.assertEqual(len(self.store.pending()), 1)
        self.assertNotIn('private-value', str(self.store.pending()))

    def test_signature_scope_and_mapping_rejected(self):
        with self.assertRaises(ValueError):
            self.receive(signature='sha256=bad')
        with self.assertRaises(ValueError):
            self.receive(issue=None)
        self.payload['installation']['id'] = 43
        with self.assertRaises(ValueError):
            self.receive()
        self.assertEqual(self.store.pending(), [])

    def test_unsigned_metadata_never_reaches_issue_resolver(self):
        calls = []
        with self.assertRaises(ValueError):
            receive(self.store, 'pull_request', b'{}', 'bad', self.secret,
                    {'owner/app': 42}, lambda *args: calls.append(args))
        self.assertEqual(calls, [])
