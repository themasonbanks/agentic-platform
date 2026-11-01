"""Verify real process-group termination using harmless local child programs."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from remediation.process import ProcessStopped, ProcessWorker


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def worker(self, program):
        return ProcessWorker((str(Path(sys.executable).resolve()), '-c', program), self.workspace)

    def test_request_is_stdin_and_host_credentials_are_not_inherited(self):
        worker = self.worker('import json,os,sys; request=json.load(sys.stdin); '
                             'print(json.dumps({"request":request,"leaked":any(name in os.environ for name in '
                             '["OPENAI_API_KEY","CODEX_API_KEY","GITHUB_TOKEN","HTTPS_PROXY"])}))')
        with patch.dict(os.environ, {name: 'fixture-only' for name in
                                    ('OPENAI_API_KEY', 'CODEX_API_KEY', 'GITHUB_TOKEN', 'HTTPS_PROXY')}):
            result = worker.run({'reference': 'literal;$(not-a-shell-command)'}, 3, lambda: True)
        self.assertEqual(json.loads(result.output), {'request': {'reference': 'literal;$(not-a-shell-command)'}, 'leaked': False})

    def test_deadline_kills_term_ignoring_parent_and_descendants(self):
        marker = self.workspace / 'escaped'
        ready = self.workspace / 'child-started'
        child = ('import pathlib,time;pathlib.Path(' + repr(str(ready)) + ').touch();'
                 'time.sleep(1.2);pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")')
        worker = self.worker('import signal,subprocess,sys,time; '
                             'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                             'subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(20)')
        started = time.monotonic()
        with self.assertRaisesRegex(ProcessStopped, 'deadline'):
            worker.run({}, .5, lambda: True)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(ready.exists())
        time.sleep(1.3)
        self.assertFalse(marker.exists())

    def test_success_also_cleans_detached_work_in_same_process_group(self):
        marker = self.workspace / 'escaped'
        ready = self.workspace / 'child-started'
        child = ('import pathlib,time;pathlib.Path(' + repr(str(ready)) + ').touch();'
                 'time.sleep(.8);pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")')
        result = self.worker('import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",' + repr(child)
                             + '],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                             'while not pathlib.Path(' + repr(str(ready)) + ').exists(): time.sleep(.01)\n'
                             'print("done")').run({}, 3, lambda: True)
        self.assertEqual(result.output.strip(), b'done')
        self.assertTrue(ready.exists())
        time.sleep(.9)
        self.assertFalse(marker.exists())

    def test_output_flood_is_bounded(self):
        with self.assertRaisesRegex(ProcessStopped, 'output'):
            self.worker('import os;os.write(1,b"x"*300000);os.write(2,b"y"*300000)').run({}, 3, lambda: True)

    def test_revocation_stops_running_process(self):
        calls = []
        def allowed():
            calls.append(True)
            return len(calls) < 4
        with self.assertRaisesRegex(ProcessStopped, 'disabled'):
            self.worker('import time;time.sleep(20)').run({}, 3, allowed)

    def test_failure_and_invalid_limits_do_not_return_success(self):
        with self.assertRaisesRegex(ProcessStopped, 'unsuccessfully'):
            self.worker('raise SystemExit(2)').run({}, 3, lambda: True)
        worker = self.worker('raise SystemExit("must not run")')
        for seconds in (0, -1, 301, True, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                worker.run({}, seconds, lambda: True)
        with self.assertRaises(ValueError):
            worker.run({'large': 'x' * 17000}, 3, lambda: True)


if __name__ == '__main__':
    unittest.main()
