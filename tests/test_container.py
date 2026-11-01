"""Run a fixed isolation probe only on an explicitly enabled disposable runner."""
import json
import os
import subprocess
import unittest
import uuid

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
