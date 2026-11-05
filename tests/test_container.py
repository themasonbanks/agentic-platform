"""Run a fixed isolation probe only on an explicitly enabled disposable runner."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from remediation.container import ContainerWorker
from remediation.checks import check_patch
from remediation.git_snapshot import GitContainerWorker
from remediation.process import ProcessStopped
from remediation.publishing import authorize_publication

IMAGE = 'busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec'


@unittest.skipUnless(os.environ.get('VERIFY_CONTAINER_ISOLATION') == '1', 'requires disposable Docker runner')
class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.name = 'review-probe-' + uuid.uuid4().hex

    def tearDown(self):
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True, timeout=20)

    def create(self, script):
        subprocess.run(['docker', 'create', '--name', self.name, '--network=none',
                        '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                        '--security-opt=no-new-privileges:true', '--pids-limit=32',
                        '--memory=64m', '--memory-swap=64m', '--cpus=0.5', '--log-driver=none',
                        IMAGE, 'sh', '-ec', script], check=True, capture_output=True, timeout=30)

    def test_runtime_boundaries(self):
        self.create('test "$(id -u)" = 65534; '
                    'test -z "${WORKER_SECRET_CANARY:-}"; '
                    'test ! -S /var/run/docker.sock; '
                    'if touch /probe-file 2>/dev/null; then exit 1; fi; '
                    'test -z "$(ip route)"; '
                    'if nc -w 1 1.1.1.1 443 </dev/null; then exit 1; fi')
        config = json.loads(subprocess.check_output(['docker', 'inspect', self.name], timeout=10))[0]
        host = config['HostConfig']
        self.assertEqual(host['PidsLimit'], 32)
        self.assertEqual(host['Memory'], 64 * 1024 * 1024)
        self.assertEqual(host['NanoCpus'], 500000000)
        self.assertTrue(host['ReadonlyRootfs'])
        self.assertFalse(host['Privileged'])
        subprocess.run(['docker', 'start', '-a', self.name], check=True, timeout=15)
        code = subprocess.check_output(['docker', 'inspect', '-f', '{{.State.ExitCode}}', self.name], timeout=10)
        self.assertEqual(code.strip(), b'0')

    def test_timeout_cleanup_removes_running_container(self):
        self.create('sleep 60')
        subprocess.run(['docker', 'start', self.name], check=True, capture_output=True, timeout=10)
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(['docker', 'wait', self.name], check=True, capture_output=True, timeout=1)
        subprocess.run(['docker', 'rm', '-f', self.name], check=True, capture_output=True, timeout=20)
        self.assertNotEqual(subprocess.run(['docker', 'inspect', self.name], capture_output=True, timeout=10).returncode, 0)


@unittest.skipUnless(os.environ.get('VERIFY_CONTAINER_ISOLATION') == '1', 'requires disposable Docker runner')
class ContainerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            if worker.last_container:
                subprocess.run(['docker', 'rm', '-f', worker.last_container], capture_output=True, timeout=10)

    def worker(self, script):
        worker = ContainerWorker(shutil.which('docker'), IMAGE, ('/bin/sh', '-ec', script),
                                 {'src/example.txt': ('100644', b'original fixture\n')})
        self.workers.append(worker)
        return worker

    def assert_removed(self, worker):
        output = subprocess.check_output(['docker', 'ps', '-aq', '--filter=name=^/' + worker.last_container + '$'], timeout=10)
        self.assertEqual(output.strip(), b'')

    def test_patch_evidence_requires_real_checks_on_fresh_tree_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {'PATH': os.defpath, 'HOME': directory, 'TMPDIR': directory,
                           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}

            def git(*arguments):
                return subprocess.check_output(['git', '-c', 'core.hooksPath=' + os.devnull,
                                                '-C', directory, *arguments],
                                               env=environment, stderr=subprocess.DEVNULL, timeout=10)

            git('init', '-q')
            (root / 'src').mkdir()
            source = root / 'src/example.txt'
            source.write_text('original\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
            head = git('rev-parse', 'HEAD').decode().strip()
            source.write_text('fixed\n')
            patch = git('diff', '--no-ext-diff')
            source.write_text('dirty host bytes\n')
            checks = {
                'first': ('/bin/sh', '-ec', 'grep -qx fixed src/example.txt && echo mutated >src/example.txt'),
                'second': ('/bin/grep', '-qx', 'fixed', 'src/example.txt'),
            }
            evidence = check_patch(root, head, patch, {'src'}, checks,
                                   shutil.which('docker'), IMAGE, 35, lambda: True)
            self.assertEqual(git('show', evidence.tree_sha + ':src/example.txt'), b'fixed\n')
            self.assertEqual(authorize_publication(head, head, evidence.tree_sha, evidence, checks)['tree'],
                             evidence.tree_sha)
            with self.assertRaises(ProcessStopped):
                check_patch(root, head, patch, {'src'}, {'failed': ('/bin/false',)},
                            shutil.which('docker'), IMAGE, 20, lambda: True)
            self.assertEqual(source.read_text(), 'dirty host bytes\n')

    def test_git_commit_is_the_container_source_despite_dirty_and_ignored_host_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {'PATH': os.defpath, 'HOME': directory, 'TMPDIR': directory,
                           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
            def git(*arguments):
                return subprocess.check_output(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', directory,
                                                *arguments], env=environment, stderr=subprocess.DEVNULL, timeout=10)
            git('init', '-q')
            (root / 'src').mkdir()
            (root / 'src/example.txt').write_bytes(b'committed fixture\n')
            (root / '.gitignore').write_text('.env.local\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
            head = git('rev-parse', 'HEAD').decode().strip()
            (root / 'src/example.txt').write_bytes(b'uncommitted private fixture\n')
            (root / '.env.local').write_text('ignored private fixture')
            worker = GitContainerWorker(root, 'fixture/app', head, shutil.which('docker'), IMAGE,
                                         ('/bin/sh', '-ec', 'test ! -e .git; test ! -e .env.local; '
                                          'cat src/example.txt; cat'))
            self.workers.append(worker)
            result = worker.run({'repository': 'fixture/app', 'head_sha': head}, 20, lambda: True)
            self.assertTrue(result.output.startswith(b'committed fixture\n'))
            self.assertNotIn(b'private fixture', result.output)
            self.assertEqual((root / 'src/example.txt').read_bytes(), b'uncommitted private fixture\n')
            self.assert_removed(worker)

    def test_mutable_source_workspace_without_host_credentials_or_root_control(self):
        worker = self.worker('test "$(id -u)" = 65534; '
                             'test -z "${WORKER_SECRET_CANARY:-}"; test ! -S /var/run/docker.sock; '
                             'test -z "$(ip route)"; if nc -w 1 1.1.1.1 443 </dev/null; then exit 1; fi; '
                             'if touch /outside 2>/dev/null; then exit 1; fi; '
                             'if kill -STOP 1 2>/dev/null; then exit 1; fi; '
                             'test "$(cat src/example.txt)" = "original fixture"; '
                             'printf "changed fixture\\n" >src/example.txt; cat src/example.txt; cat')
        configurations = []
        def observe():
            if worker.last_container and not configurations:
                probe = subprocess.run(['docker', 'inspect', worker.last_container], capture_output=True, timeout=2)
                if probe.returncode == 0:
                    configurations.append(json.loads(probe.stdout)[0])
            return True
        result = worker.run({'reference': 'fixture-only'}, 15, observe)
        self.assertTrue(result.output.startswith(b'changed fixture\n'))
        self.assertIn(b'"reference": "fixture-only"', result.output)
        self.assertEqual(len(configurations), 1)
        host = configurations[0]['HostConfig']
        self.assertEqual(host['Memory'], 512 * 1024 * 1024)
        self.assertEqual(host['MemorySwap'], host['Memory'])
        self.assertEqual(host['PidsLimit'], 64)
        self.assertEqual(host['NanoCpus'], 1_000_000_000)
        self.assertEqual(host['NetworkMode'], 'none')
        self.assertEqual(host['PidMode'], '')
        self.assertTrue(host['ReadonlyRootfs'])
        self.assertFalse(host['Privileged'])
        self.assertFalse(host['Binds'])
        self.assertEqual(configurations[0]['Config']['User'], '65533:65533')
        self.assert_removed(worker)

    def test_deadline_and_detached_children_are_removed_with_the_container(self):
        worker = self.worker('touch /workspace/started; setsid /bin/sh -c "sleep 30" >/dev/null 2>&1 & sleep 30')
        started = time.monotonic()
        ready = []
        def observe():
            if worker.last_container and not ready:
                probe = subprocess.run(['docker', 'exec', '--user=65534:65534', worker.last_container,
                                        '/bin/test', '-f', '/workspace/started'], capture_output=True, timeout=2)
                if probe.returncode == 0:
                    ready.append(True)
            return True
        with self.assertRaises(ProcessStopped):
            worker.run({}, 6, observe)
        self.assertTrue(ready, 'deadline test must reach the workload before it stops')
        self.assertLess(time.monotonic() - started, 12)
        self.assert_removed(worker)

    def test_output_flood_and_revocation_remove_the_container(self):
        worker = self.worker('head -c 300000 /dev/zero')
        with self.assertRaisesRegex(ProcessStopped, 'output'):
            worker.run({}, 15, lambda: True)
        self.assert_removed(worker)
        worker = self.worker('sleep 30')
        started = time.monotonic()
        with self.assertRaises(ProcessStopped):
            worker.run({}, 15, lambda: time.monotonic() - started < 3)
        self.assert_removed(worker)

    def test_watchdog_stops_container_after_controller_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'container-name'
            program = ('import pathlib,threading,time\n'
                       'from remediation.container import ContainerWorker\n'
                       'worker=ContainerWorker(' + repr(shutil.which('docker')) + ',' + repr(IMAGE)
                       + ',("/bin/sh","-ec","touch /workspace/started; exec sleep 30"),'
                       '{"src/file":("100644",b"fixture")})\n'
                       'def record():\n'
                       ' while worker.last_container is None: time.sleep(.01)\n'
                       ' pathlib.Path(' + repr(str(marker)) + ').write_text(worker.last_container)\n'
                       'threading.Thread(target=record,daemon=True).start()\n'
                       'worker.run({},8,lambda:True)\n')
            environment = dict(os.environ, TMPDIR=directory)
            child = subprocess.Popen([sys.executable, '-c', program], env=environment,
                                     cwd=Path(__file__).resolve().parents[1],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            name = None
            try:
                until = time.monotonic() + 6
                while not marker.exists() and time.monotonic() < until:
                    time.sleep(.05)
                self.assertTrue(marker.exists(), 'controller did not initialize')
                name = marker.read_text()
                started = False
                while time.monotonic() < until:
                    probe = subprocess.run(['docker', 'exec', '--user=65534:65534', name,
                                            '/bin/test', '-f', '/workspace/started'],
                                           capture_output=True, timeout=2)
                    if probe.returncode == 0:
                        started = True
                        break
                    time.sleep(.1)
                self.assertTrue(started, 'container command did not start')
                child.kill()
                child.wait(timeout=3)
                until = time.monotonic() + 10
                state = b'true'
                while state == b'true' and time.monotonic() < until:
                    state = subprocess.check_output(['docker', 'inspect', '-f', '{{.State.Running}}', name],
                                                    timeout=2).strip()
                    if state == b'true':
                        time.sleep(.1)
                self.assertEqual(state, b'false')
                code = subprocess.check_output(['docker', 'inspect', '-f', '{{.State.ExitCode}}', name], timeout=2)
                self.assertEqual(code.strip(), b'0')
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)
                if name:
                    subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
