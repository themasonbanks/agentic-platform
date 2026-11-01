"""Verify protobuf transport acknowledgements against a controlled HTTP receiver."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import closing
import hashlib
import hmac
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest, ExportTraceServiceResponse)
from telemetry.events import EventStore
from telemetry.otel import export_pending
from telemetry.otlp import OtlpExporter
from telemetry.mapping import IssueResolver
from telemetry.metrics import render_metrics
from telemetry.reviews import InlineReviewEvidence
from telemetry.service import create_application
from telemetry.sources import codex_turn


class OtlpTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        self.store.append('github', 'fixture', 'owner/app', 'KEB-130', 'opened', 1000000, {'pr': 3})
        self.status, self.response, self.requests = 200, b'', []
        owner = self
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, self.headers,
                                       self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(owner.status)
                self.send_header('Location', '/redirected')
                self.send_header('Content-Length', str(len(owner.response)))
                self.end_headers()
                self.wfile.write(owner.response)

            def log_message(self, *args):
                pass
        with patch('socket.getfqdn', return_value='localhost'):
            server = HTTPServer(('127.0.0.1', 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(close)
        self.exporter = OtlpExporter(f'http://127.0.0.1:{server.server_port}/v1/traces',
                                     None, '127.0.0.1', local_fixture=True)
        self.addCleanup(self.exporter.shutdown)

    def test_success_delivers_real_protobuf_and_acknowledges_outbox(self):
        self.assertEqual(export_pending(self.store, self.exporter), 1)
        path, headers, body = self.requests[0]
        self.assertEqual(path, '/v1/traces')
        self.assertEqual(headers['Content-Type'], 'application/x-protobuf')
        self.assertIsNone(headers.get('Authorization'))
        request = ExportTraceServiceRequest.FromString(body)
        span = request.resource_spans[0].scope_spans[0].spans[0]
        self.assertEqual(span.name, 'engineering.github.opened')
        self.assertEqual(self.store.pending(), [])

    def test_redirect_error_and_partial_rejection_keep_outbox_pending(self):
        partial = ExportTraceServiceResponse()
        partial.partial_success.rejected_spans = 1
        for status, response in [(302, b''), (401, b''), (503, b''),
                                 (200, partial.SerializeToString()), (200, b'invalid protobuf')]:
            with self.subTest(status=status, response=response):
                self.status, self.response = status, response
                before = len(self.requests)
                self.assertEqual(export_pending(self.store, self.exporter), 0)
                self.assertEqual(len(self.store.pending()), 1)
                self.assertEqual(len(self.requests), before + 1)

    def test_destination_and_credential_validation(self):
        for endpoint, credential, host in [
                ('http://example.com/v1/traces', 'Basic fixture', 'example.com'),
                ('https://other.example/v1/traces', 'Basic fixture', 'example.com'),
                ('https://example.com/v1/traces?token=fixture', 'Basic fixture', 'example.com'),
                ('https://example.com/v1/traces', 'Basic fixture\r\nOther: value', 'example.com')]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                OtlpExporter(endpoint, credential, host)

    def test_four_sources_flow_through_durable_store_to_correlated_otlp_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/events.db'
            secret = b'local-integration-fixture-secret-' * 2
            resolver = IssueResolver({('owner/app', 3): 'KEB-130'}, {})
            app = create_application(lambda: sqlite3.connect(path), secret, {'owner/app': 42},
                                     resolver, secret, 'org', {'issue': ('owner/app', 'KEB-130')},
                                     {'state': 'done'}, now_ms=lambda: 1000000,
                                     inline_evidence=InlineReviewEvidence(lambda route: [
                                         {'id': 91, 'pull_request_review_id': 17, 'path': 'src/example.py',
                                          'position': 4, 'body': 'private-fixture-marker'}]
                                         if '/reviews/17/' in route else []))
            def deliver(route, payload, event=None):
                body = json.dumps(payload).encode()
                signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
                env = {'PATH_INFO': route, 'REQUEST_METHOD': 'POST',
                       'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)),
                       'wsgi.input': io.BytesIO(body)}
                if event:
                    env.update(HTTP_X_GITHUB_EVENT=event, HTTP_X_HUB_SIGNATURE_256='sha256=' + signature)
                else:
                    env['HTTP_LINEAR_SIGNATURE'] = signature
                statuses = []
                list(app(env, lambda status, headers: statuses.append(status)))
                self.assertEqual(statuses, ['200 OK'])
            repo = {'repository': {'full_name': 'owner/app'}, 'installation': {'id': 42}}
            deliver('/webhooks/github', dict(repo, action='opened', pull_request={
                'number': 3, 'merged': False, 'state': 'open', 'body': 'private-fixture-marker',
                'updated_at': '2026-11-01T12:00:00Z', 'head': {'sha': 'a' * 40}}), 'pull_request')
            deliver('/webhooks/github', dict(repo, workflow_run={
                'id': 9, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
                'head_sha': 'a' * 40, 'updated_at': '2026-11-01T12:01:00Z',
                'pull_requests': [{'number': 3}]}), 'workflow_run')
            for review_id in (17, 18):
                review = dict(repo, action='submitted', pull_request={
                    'number': 3, 'head': {'sha': 'a' * 40}}, review={
                    'id': review_id, 'state': 'commented', 'commit_id': 'a' * 40,
                    'submitted_at': '2026-11-01T12:01:30Z', 'body': 'private-fixture-marker'})
                deliver('/webhooks/github', review, 'pull_request_review')
                deliver('/webhooks/github', review, 'pull_request_review')
            deliver('/webhooks/linear', {'organizationId': 'org', 'webhookTimestamp': 1000000,
                                        'type': 'Issue', 'action': 'update', 'data': {
                                            'id': 'issue', 'stateId': 'state',
                                            'updatedAt': '2026-11-01T12:02:00Z',
                                            'description': 'private-fixture-marker'}})
            with closing(sqlite3.connect(path)) as db:
                store = EventStore(db)
                codex_turn(store, {'type': 'turn.completed', 'usage': {'input_tokens': 11,
                           'output_tokens': 4}, 'prompt': 'private-fixture-marker'},
                           'turn', 'owner/app', 'KEB-130', 'run', 'a' * 40, 1000000)
                self.assertEqual(export_pending(store, self.exporter), 6)
                self.assertEqual(store.pending(), [])
                metrics = render_metrics(db, ['owner/app'])
                self.assertIn('type="input"} 11\n', metrics)
                self.assertIn('attempt="1",outcome="success"} 1\n', metrics)
                self.assertIn('agentic_review_cycles_total{repository="owner/app"} 1\n', metrics)
                self.assertIn('agentic_review_unclassified_total{repository="owner/app"} 1\n', metrics)
            spans = [ExportTraceServiceRequest.FromString(body).resource_spans[0].scope_spans[0].spans[0]
                     for _, _, body in self.requests]
            self.assertEqual(len({span.trace_id for span in spans}), 1)
            self.assertEqual(len({span.span_id for span in spans}), 6)
            self.assertEqual({span.name.split('.')[1] for span in spans}, {'agent', 'ci', 'github', 'linear'})
            self.assertNotIn(b'private-fixture-marker', b''.join(body for _, _, body in self.requests))
