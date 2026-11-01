"""Exercise HTTP transport rejection and durable signed delivery replay."""
import hashlib
import hmac
import io
import json
import sqlite3
import unittest
from telemetry.events import EventStore
from telemetry.http import application
from telemetry.linear import receive_issue


class HttpTests(unittest.TestCase):
    def request(self, app, body=b'{}', **changes):
        environ = {'PATH_INFO': '/linear', 'REQUEST_METHOD': 'POST',
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)),
                   'wsgi.input': io.BytesIO(body)}
        environ.update(changes)
        response = []
        result = b''.join(app(environ, lambda status, headers: response.append(status)))
        return response[0], result

    def test_transport_rejects_before_receiver(self):
        calls = []
        app = application({'/linear': lambda *args: calls.append(args)})
        for changes, expected in [({'REQUEST_METHOD': 'GET'}, '405'),
                                  ({'CONTENT_TYPE': 'text/plain'}, '415'),
                                  ({'CONTENT_LENGTH': '-1'}, '400'),
                                  ({'CONTENT_LENGTH': '1048577'}, '413'),
                                  ({'CONTENT_LENGTH': '3'}, '400'),
                                  ({'PATH_INFO': '/other'}, '404')]:
            with self.subTest(changes=changes):
                self.assertTrue(self.request(app, **changes)[0].startswith(expected))
        self.assertEqual(calls, [])

    def test_signed_linear_delivery_and_duplicate_return_200(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        store = EventStore(db)
        secret = b'fixture-only-webhook-secret' * 2
        body = json.dumps({'webhookTimestamp': 1000000, 'organizationId': 'org',
                           'type': 'Issue', 'action': 'update', 'data': {
                               'id': 'issue', 'stateId': 'state',
                               'updatedAt': '2026-11-01T12:00:00Z'}}).encode()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        app = application({'/linear': lambda raw, env: receive_issue(
            store, raw, env.get('HTTP_LINEAR_SIGNATURE'), secret, 'org',
            {'issue': ('owner/app', 'KEB-130')}, {'state': 'done'}, 1000000)})
        for _ in range(2):
            self.assertEqual(self.request(app, body, HTTP_LINEAR_SIGNATURE=signature)[0], '200 OK')
        self.assertEqual(len(store.pending()), 1)
        self.assertEqual(self.request(app, body, HTTP_LINEAR_SIGNATURE='bad')[0], '400 Bad Request')

    def test_database_failure_is_retryable_without_error_details(self):
        def fail(*args):
            raise sqlite3.OperationalError('private database location')
        status, body = self.request(application({'/linear': fail}))
        self.assertEqual(status, '503 Service Unavailable')
        self.assertNotIn(b'private', body)
