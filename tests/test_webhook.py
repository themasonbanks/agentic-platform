"""Verify bounded HTTP intake and generic failures without a network listener."""
import io
import unittest
from remediation.webhook import Webhook


class IntakeStub:
    def __init__(self):
        self.calls = []
        self.error = None

    def receive(self, event, signature, body):
        self.calls.append((event, signature, body))
        if self.error:
            raise self.error
        return 'queued'


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.intake = IntakeStub()
        self.app = Webhook(self.intake)

    def request(self, **overrides):
        env = {'PATH_INFO': '/webhooks/github', 'REQUEST_METHOD': 'POST',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '2',
               'wsgi.input': io.BytesIO(b'{}'), 'HTTP_X_GITHUB_EVENT': 'pull_request_review_comment',
               'HTTP_X_HUB_SIGNATURE_256': 'sha256=fixture'}
        env.update(overrides)
        metadata = []
        body = b''.join(self.app(env, lambda status, headers: metadata.append((status, headers))))
        return metadata[0][0], body

    def test_exact_raw_bytes_forwarded(self):
        self.assertEqual(self.request()[0], '202 Accepted')
        self.assertEqual(self.intake.calls, [('pull_request_review_comment', 'sha256=fixture', b'{}')])

    def test_size_and_truncation_rejected_before_intake(self):
        for length in ('', '-1', '0', '1048577', 'not-a-number', '3'):
            with self.subTest(length=length):
                self.assertNotEqual(self.request(CONTENT_LENGTH=length)[0], '202 Accepted')
        self.assertEqual(self.intake.calls, [])

    def test_route_method_and_media_type(self):
        self.assertEqual(self.request(PATH_INFO='/other')[0], '404 Not Found')
        self.assertEqual(self.request(REQUEST_METHOD='GET')[0], '405 Method Not Allowed')
        self.assertEqual(self.request(CONTENT_TYPE='text/plain')[0], '415 Unsupported Media Type')
        self.assertEqual(self.intake.calls, [])

    def test_errors_never_echo_sensitive_details(self):
        for error, expected in [(ValueError('private-value'), '400 Bad Request'),
                                (RuntimeError('private-value'), '503 Service Unavailable')]:
            self.intake.error = error
            status, body = self.request()
            self.assertEqual(status, expected)
            self.assertNotIn(b'private-value', body)

    def test_real_signed_intake_persists_once_and_rejects_tampering(self):
        import hashlib
        import hmac
        import json
        import sqlite3
        from types import SimpleNamespace
        from remediation.intake import Intake, Review
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        secret = b'fixture-only-secret-material-' * 2
        review = Review('a' * 40, 'owner/app', 'fix/example', True, 'a' * 40,
                        123, True, 'src/example.py', 4, 'Check boundary value.', False, False)
        source = SimpleNamespace(current_review=lambda *args: review)
        self.app = Webhook(Intake(db, secret, {'owner/app': 42}, 123, source))
        body = json.dumps({'action': 'created', 'repository': {'full_name': 'owner/app'},
                           'installation': {'id': 42}, 'pull_request': {'number': 5},
                           'comment': {'id': 10}}).encode()
        signature = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
        for _ in range(2):
            status, _ = self.request(CONTENT_LENGTH=str(len(body)), **{
                'wsgi.input': io.BytesIO(body), 'HTTP_X_HUB_SIGNATURE_256': signature})
            self.assertEqual(status, '202 Accepted')
        self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        status, _ = self.request(CONTENT_LENGTH=str(len(body)), **{
            'wsgi.input': io.BytesIO(body), 'HTTP_X_HUB_SIGNATURE_256': 'sha256=bad'})
        self.assertEqual(status, '400 Bad Request')
        self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
