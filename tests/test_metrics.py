"""Verify seeded repository totals, missing usage, replay and ambiguous outcomes."""
import sqlite3
import tempfile
import unittest
from telemetry.events import EventStore
from telemetry.metrics import render_metrics
from telemetry.metrics_http import create_metrics_application


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)

    def add(self, source, identity, kind, attrs, repo='owner/app'):
        return self.store.append(source, identity, repo, 'KEB-130', kind, 1000000, attrs)

    def test_seeded_ci_first_attempt_retry_and_repository_scope(self):
        self.add('ci', 'one', 'failure', {'run_id': '1', 'retry_count': 0})
        self.add('ci', 'retry', 'success', {'run_id': '1', 'retry_count': 1})
        self.add('ci', 'two', 'success', {'run_id': '2', 'retry_count': 0})
        self.add('ci', 'skip', 'skipped', {'run_id': '3', 'retry_count': 0})
        self.add('ci', 'other', 'success', {'run_id': '1', 'retry_count': 0}, 'owner/other')
        self.assertFalse(self.add('ci', 'two', 'success', {'run_id': '2', 'retry_count': 0}))
        text = render_metrics(self.db, ['owner/app'], now_ns=2_000_000_000_000_000_000)
        self.assertIn('agentic_ci_attempts_total{repository="owner/app",attempt="1",outcome="success"} 1\n', text)
        self.assertIn('agentic_ci_attempts_total{repository="owner/app",attempt="1",outcome="failure"} 1\n', text)
        self.assertIn('agentic_ci_attempts_total{repository="owner/app",attempt="retry",outcome="success"} 1\n', text)
        self.assertNotIn('owner/other', text)
        self.assertEqual(text, render_metrics(self.db, ['owner/app'], now_ns=2_000_000_000_000_000_000))

    def test_usage_missing_is_omitted_and_export_ack_does_not_reset_totals(self):
        self.add('agent', 'one', 'success', {'input_tokens': 10, 'output_tokens': 2})
        self.add('agent', 'two', 'success', {'input_tokens': 5})
        self.add('agent', 'missing', 'success', {})
        text = render_metrics(self.db, ['owner/app'], now_ns=2_000_000_000_000_000_000)
        self.assertIn('agentic_tokens_total{repository="owner/app",type="input"} 15\n', text)
        self.assertIn('agentic_tokens_total{repository="owner/app",type="output"} 2\n', text)
        self.assertNotIn('agentic_cost_usd_total', text)
        self.store.acknowledge('agent', 'one')
        self.assertEqual(text, render_metrics(self.db, ['owner/app'], now_ns=2_000_000_000_000_000_000))
        self.assertNotIn('agentic_tokens_total{', render_metrics(self.db, ['owner/other']))

    def test_limit_and_conflicting_terminal_attempt_fail_closed(self):
        attrs = {'run_id': '1', 'retry_count': 0}
        self.add('ci', 'one', 'success', attrs)
        self.add('ci', 'duplicate-metadata', 'success', attrs)
        self.assertIn('outcome="success"} 1\n', render_metrics(self.db, ['owner/app']))
        with self.assertRaises(ValueError):
            render_metrics(self.db, ['owner/app'], max_events=1)
        self.add('ci', 'conflict', 'failure', attrs)
        with self.assertRaises(ValueError):
            render_metrics(self.db, ['owner/app'])

    def test_authenticated_scrape_reopens_durable_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/events.db'
            db = sqlite3.connect(path)
            EventStore(db).append('agent', 'fixture', 'owner/app', 'KEB-130', 'success',
                                  1000000, {'input_tokens': 7})
            db.close()
            calls = []
            def connect():
                calls.append(True)
                return sqlite3.connect('file:' + path + '?mode=ro', uri=True)
            app = create_metrics_application(connect, 'fixture-token-' * 4, ['owner/app'])
            def request(authorization):
                result = []
                body = b''.join(app({'PATH_INFO': '/metrics', 'REQUEST_METHOD': 'GET',
                                     'HTTP_AUTHORIZATION': authorization},
                                    lambda status, headers: result.append((status, headers))))
                return result[0], body
            self.assertEqual(request('Bearer wrong')[0][0], '401 Unauthorized')
            self.assertEqual(calls, [])
            (status, headers), body = request('Bearer ' + 'fixture-token-' * 4)
            self.assertEqual(status, '200 OK')
            self.assertIn(('Content-Type', 'text/plain; version=0.0.4; charset=utf-8'), headers)
            self.assertIn(b'type="input"} 7\n', body)
            self.assertEqual(len(calls), 1)
