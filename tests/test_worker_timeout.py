"""Exercise the actual timeout wrapper without credentials or an external exporter."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


class TimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wrapper = Path(__file__).resolve().parents[1] / 'scripts/run_telemetry_export.sh'
        self.environment = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                                AGENTIC_EXPORT_TIMEOUT_SECONDS='1')

    def stub(self, body):
        executable = self.root / 'python3'
        executable.write_text('#!/bin/sh\n' + body)
        executable.chmod(0o700)

    def run_wrapper(self):
        return subprocess.run(['bash', str(self.wrapper)], env=self.environment,
                              capture_output=True, timeout=10, check=False)

    def test_successful_worker_exit_preserved(self):
        self.stub('exit 75\n')
        self.assertEqual(self.run_wrapper().returncode, 75)

    def test_invalid_deadline_never_starts_worker(self):
        self.stub('echo worker-started\n')
        for value in ('0', '241', '-1', '1;echo unsafe', '999999999999999999'):
            self.environment['AGENTIC_EXPORT_TIMEOUT_SECONDS'] = value
            result = self.run_wrapper()
            self.assertEqual(result.returncode, 64)
            self.assertNotIn(b'worker-started', result.stdout)

    def test_deadline_terminates_worker_and_descendant(self):
        marker = self.root / 'escaped'
        self.environment['FIXTURE_MARKER'] = str(marker)
        self.stub('sh -c \'sleep 3; echo escaped > "$FIXTURE_MARKER"\' &\nwait\n')
        started = time.monotonic()
        self.assertEqual(self.run_wrapper().returncode, 124)
        self.assertLess(time.monotonic() - started, 4)
        time.sleep(3)
        self.assertFalse(marker.exists())

    def test_worker_ignoring_term_is_force_killed(self):
        self.stub("trap '' TERM\nsleep 20\n")
        started = time.monotonic()
        self.assertIn(self.run_wrapper().returncode, (137, -9))
        self.assertLess(time.monotonic() - started, 9)
