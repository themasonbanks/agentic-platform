"""Verify durable timing, replay freshness and clock anomalies without estimates."""
import json
import sqlite3
import unittest
from telemetry.events import EventStore
from telemetry.metrics import render_metrics


SECOND = 1_000_000_000


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.now = 210 * SECOND
        self.store = EventStore(self.db, clock=lambda: self.now)

    def add(self, identity, observed, source='github', repo='owner/app'):
        return self.store.append(source, identity, repo, 'KEB-130', 'opened', observed * SECOND, {})

    def sample(self, text, metric, value, source='github'):
        self.assertIn(f'{metric}{{repository="owner/app",source="{source}"}} {value}\n', text)

    def test_delivery_lag_and_idle_age_are_separate_and_replays_do_not_refresh(self):
        self.add('one', 200)
        self.now = 220 * SECOND
        self.assertFalse(self.add('one', 200))
        text = render_metrics(self.db, ['owner/app'], now_ns=230 * SECOND)
        self.sample(text, 'agentic_source_lag_seconds', 10)
        self.sample(text, 'agentic_source_receipt_age_seconds', 20)
        self.sample(text, 'agentic_source_timing_valid', 1)
        self.store.acknowledge('github', 'one')
        self.assertEqual(text, render_metrics(self.db, ['owner/app'], now_ns=230 * SECOND))
        self.assertEqual(self.db.execute('SELECT count(*) FROM engineering_receipts').fetchone()[0], 1)

    def test_late_source_timestamp_uses_actual_receipt_order_not_source_time(self):
        self.add('z-newer-source', 200)
        self.now = 220 * SECOND
        self.add('a-late-source', 100)
        text = render_metrics(self.db, ['owner/app'], now_ns=230 * SECOND)
        self.sample(text, 'agentic_source_lag_seconds', 120)
        self.sample(text, 'agentic_source_receipt_age_seconds', 10)

    def test_future_source_and_backwards_receiver_clock_are_not_healthy_zero_lag(self):
        self.add('future', 230)
        text = render_metrics(self.db, ['owner/app'], now_ns=240 * SECOND)
        self.sample(text, 'agentic_source_timing_valid', 0)
        self.sample(text, 'agentic_source_clock_skew_seconds', 20)
        self.assertNotIn('agentic_source_lag_seconds{', text)
        self.now = 200 * SECOND
        self.add('clock-went-backwards', 190)
        text = render_metrics(self.db, ['owner/app'], now_ns=195 * SECOND)
        self.sample(text, 'agentic_source_timing_valid', 0)
        self.assertNotIn('agentic_source_receipt_age_seconds{', text)
        self.assertNotIn('agentic_source_lag_seconds{', text)
        text = render_metrics(self.db, ['owner/app'], now_ns=205 * SECOND)
        self.sample(text, 'agentic_source_timing_valid', 0)
        self.assertNotIn('agentic_source_lag_seconds{', text)
        self.now = 215 * SECOND
        self.add('clock-caught-up', 200)
        text = render_metrics(self.db, ['owner/app'], now_ns=220 * SECOND)
        self.sample(text, 'agentic_source_lag_seconds', 15)

    def test_sources_repositories_and_recreated_store_keep_distinct_timing(self):
        self.add('one', 200)
        self.now = 220 * SECOND
        self.add('two', 215, 'ci')
        self.add('other', 100, repo='owner/other')
        EventStore(self.db, clock=lambda: 500 * SECOND)
        text = render_metrics(self.db, ['owner/app'], now_ns=230 * SECOND)
        self.sample(text, 'agentic_source_lag_seconds', 10)
        self.sample(text, 'agentic_source_lag_seconds', 5, 'ci')
        self.assertNotIn('owner/other', text)

    def test_invalid_receiver_clock_rolls_back_new_event_and_receipt(self):
        for now in (True, -1, 1.5, 9_223_372_036_854_775_808):
            self.now = now
            with self.assertRaises(ValueError):
                self.add('one', 200)
        self.assertEqual(self.db.execute('SELECT count(*) FROM engineering_events').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM engineering_receipts').fetchone()[0], 0)
        for now in (True, -1, 1.5):
            with self.assertRaises(ValueError):
                render_metrics(self.db, ['owner/app'], now_ns=now)

    def test_existing_event_schema_gets_no_invented_legacy_receipt(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('''CREATE TABLE engineering_events(source TEXT NOT NULL,event_id TEXT NOT NULL,
                observed_ns INTEGER NOT NULL,trace_id TEXT NOT NULL,span_id TEXT NOT NULL,
                payload TEXT NOT NULL,exported INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(source,event_id))''')
            event = {'source': 'github', 'event_id': 'legacy', 'repository': 'owner/app', 'issue': 'KEB-130',
                     'kind': 'opened', 'observed_ns': 200 * SECOND, 'attributes': {},
                     'cost_known': False, 'usage_known': False}
            db.execute('INSERT INTO engineering_events VALUES(?,?,?,?,?,?,0)',
                       ('github', 'legacy', 200 * SECOND, 'a' * 32, 'b' * 16, json.dumps(event, sort_keys=True)))
            db.commit()
            store = EventStore(db, clock=lambda: 210 * SECOND)
            self.assertFalse(store.append('github', 'legacy', 'owner/app', 'KEB-130', 'opened', 200 * SECOND, {}))
            text = render_metrics(db, ['owner/app'], now_ns=230 * SECOND)
            self.sample(text, 'agentic_source_timing_valid', 0)
            self.assertNotIn('agentic_source_lag_seconds{', text)
            self.assertEqual(db.execute('SELECT count(*) FROM engineering_receipts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
