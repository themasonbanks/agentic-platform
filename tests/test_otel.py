"""Verify SDK trace correlation, privacy scope and failed-export retention."""
import sqlite3
import unittest
from opentelemetry.sdk.trace.export import SpanExportResult, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from telemetry.events import EventStore
from telemetry.otel import export_pending


class FailingExporter(SpanExporter):
    def export(self, spans):
        return SpanExportResult.FAILURE


class OtelTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = EventStore(self.db)
        for source in ('agent', 'ci', 'github', 'linear'):
            self.store.append(source, 'fixture', 'owner/app', 'KEB-130', 'success', 1000000000,
                              {'duration_ms': 2})

    def test_sdk_emits_correlated_metadata_spans(self):
        receiver = InMemorySpanExporter()
        self.assertEqual(export_pending(self.store, receiver), 4)
        spans = receiver.get_finished_spans()
        self.assertEqual(len(spans), 4)
        self.assertEqual(len({s.context.trace_id for s in spans}), 1)
        self.assertEqual({s.attributes['source'] for s in spans}, {'agent', 'ci', 'github', 'linear'})
        self.assertTrue(all(s.attributes['cost_known'] is False for s in spans))
        self.assertTrue(all('prompt' not in s.attributes for s in spans))
        self.assertEqual(self.store.pending(), [])

    def test_failure_keeps_outbox_pending(self):
        self.assertEqual(export_pending(self.store, FailingExporter()), 0)
        self.assertEqual(len(self.store.pending()), 4)
