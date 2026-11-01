"""Verify correlation, out-of-order retention, unknown cost and privacy refusal."""
import sqlite3
import unittest
from telemetry.events import EventStore


class EventTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.store = EventStore(self.db)
        self.addCleanup(self.db.close)

    def append(self, source='agent', event_id='one', timestamp=2, attributes=None):
        return self.store.append(source, event_id, 'owner/app', 'KEB-130', 'success', timestamp, attributes or {})

    def test_four_sources_share_trace_with_distinct_spans(self):
        for source in ('agent', 'ci', 'github', 'linear'):
            self.append(source=source)
        events = self.store.pending()
        self.assertEqual(len({e['trace_id'] for e in events}), 1)
        self.assertEqual(len({e['span_id'] for e in events}), 4)

    def test_duplicates_conflicts_and_order(self):
        self.assertTrue(self.append())
        self.assertFalse(self.append())
        with self.assertRaises(ValueError):
            self.append(timestamp=3)
        self.append(event_id='older', timestamp=1)
        self.assertEqual([e['event_id'] for e in self.store.pending()], ['older', 'one'])

    def test_unknown_cost_is_not_zero_actual(self):
        self.append()
        event = self.store.pending()[0]
        self.assertFalse(event['cost_known'])
        self.assertFalse(event['usage_known'])
        self.assertNotIn('cost_microusd', event['attributes'])

    def test_private_fields_never_persisted(self):
        for key in ('prompt', 'body', 'email', 'token', 'source_code'):
            with self.assertRaises(ValueError):
                self.append(attributes={key: 'private-value'})
        self.assertEqual(self.store.pending(), [])

    def test_acknowledgement_survives_store_recreation(self):
        self.append()
        self.assertEqual(len(EventStore(self.db).pending()), 1)
        self.store.acknowledge('agent', 'one')
        self.assertEqual(EventStore(self.db).pending(), [])
