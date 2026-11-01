"""Verify operator batches persist acknowledgement and avoid credential output."""
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from telemetry.events import EventStore
from telemetry.worker import main, run_once


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'events.db'
        db = sqlite3.connect(self.path)
        store = EventStore(db)
        store.append('agent', 'fixture', 'owner/app', 'KEB-130', 'success', 1, {})
        db.close()
        self.path.chmod(0o600)

    def test_bounded_batch_survives_worker_recreation_and_closes_exporter(self):
        exporters = []
        class Exporter(InMemorySpanExporter):
            closed = False
            def shutdown(self):
                self.closed = True
                super().shutdown()
        def factory(*args):
            exporter = Exporter()
            exporters.append(exporter)
            return exporter
        self.assertEqual(run_once(self.path, 'fixture', 'private-fixture', 'fixture',
                                  exporter_factory=factory), {'delivered': 1, 'pending': 0, 'quarantined': 0})
        self.assertEqual(run_once(self.path, 'fixture', 'private-fixture', 'fixture',
                                  exporter_factory=factory), {'delivered': 0, 'pending': 0, 'quarantined': 0})
        self.assertTrue(all(exporter.closed for exporter in exporters))

    def test_unprotected_or_symlink_database_rejected_before_exporter(self):
        self.path.chmod(0o644)
        calls = []
        with self.assertRaises(ValueError):
            run_once(self.path, '', '', '', exporter_factory=lambda *args: calls.append(args))
        self.path.chmod(0o600)
        link = self.path.parent / 'link.db'
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            run_once(link, '', '', '', exporter_factory=lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_cli_configuration_failure_is_generic(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'AGENTIC_OTLP_AUTHORIZATION': 'private-fixture'}, clear=True):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(), 75)
        self.assertEqual(output.getvalue(), '{"status": "unavailable"}\n')

    def test_cli_reports_partial_failure_for_retry(self):
        config = dict.fromkeys(('AGENTIC_TELEMETRY_DB', 'AGENTIC_OTLP_TRACES_ENDPOINT',
                               'AGENTIC_OTLP_AUTHORIZATION', 'AGENTIC_OTLP_HOST'), 'fixture')
        with patch.dict(os.environ, config, clear=True), patch('telemetry.worker.run_once') as run:
            run.return_value = {'delivered': 1, 'pending': 1, 'quarantined': 0}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 75)
            run.return_value = {'delivered': 100, 'pending': 1, 'quarantined': 0}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            run.return_value = {'delivered': 0, 'pending': 0, 'quarantined': 1}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 75)
