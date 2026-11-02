"""Exercise signature/replay/scope checks and issue-state privacy filtering."""
import hashlib
import hmac
import json
import sqlite3
import unittest
from telemetry.events import EventStore
from telemetry.linear import receive_issue


class LinearTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        self.secret = b'local-only-fixture-secret-' * 2
        self.payload = {'webhookTimestamp': 1000000, 'organizationId': 'org-fixture',
                        'type': 'Issue', 'action': 'update', 'data': {
                            'id': 'issue-fixture', 'stateId': 'state-fixture',
                            'updatedAt': '2026-11-01T12:00:00Z', 'title': 'private-value',
                            'description': 'private-value'}, 'actor': {'email': 'private-value'}}

    def receive(self, now=1000000, signature=None, organization='org-fixture', issues=None):
        body = json.dumps(self.payload).encode()
        signature = signature if signature is not None else hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return receive_issue(self.store, body, signature, self.secret, organization,
                             {'issue-fixture': ('owner/app', 'KEB-130')} if issues is None else issues,
                             {'state-fixture': 'in_progress'}, now)

    def test_verified_state_only_and_deduplication(self):
        self.assertTrue(self.receive())
        self.assertFalse(self.receive())
        events = self.store.pending()
        self.assertEqual(events[0]['kind'], 'in_progress')
        self.assertNotIn('private-value', str(events))

    def test_signature_replay_and_scope_fail_before_storage(self):
        for kwargs in ({'signature': 'bad'}, {'now': 1060001}, {'now': 939999},
                       {'organization': 'other-org'}, {'issues': {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.receive(**kwargs)
        self.assertEqual(self.store.pending(), [])

    def test_unknown_state_and_boolean_timestamp_rejected(self):
        self.payload['data']['stateId'] = 'unknown'
        with self.assertRaises(ValueError):
            self.receive()
        self.payload['webhookTimestamp'] = True
        with self.assertRaises(ValueError):
            self.receive()
