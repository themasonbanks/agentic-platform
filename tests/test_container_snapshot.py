"""Verify source archive paths, modes and bounds without reading host files."""
import io
import tarfile
import unittest
from remediation.container import ContainerWorker, snapshot_archive
from remediation.process import ProcessStopped

IMAGE = 'busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec'


class SnapshotTests(unittest.TestCase):
    def test_archive_contains_only_explicit_ordinary_source_bytes(self):
        files = {'src/example.py': ('100644', b'print("fixture")\n'),
                 'scripts/check.sh': ('100755', b'python3 -m unittest\n')}
        raw = snapshot_archive(files)
        files['src/example.py'] = ('100644', b'later mutation')
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertEqual(archive.extractfile('src/example.py').read(), b'print("fixture")\n')
            self.assertEqual(archive.getmember('scripts/check.sh').mode, 0o755)
            self.assertTrue(all(item.isfile() or item.isdir() for item in archive.getmembers()))
            self.assertTrue(all(item.uid == item.gid == 65534 for item in archive.getmembers()))

    def test_sensitive_noncanonical_and_conflicting_paths_are_rejected(self):
        for name in ('/etc/passwd', '../outside', 'src/../outside', './src/file', 'src//file',
                     'src\\file', 'src/file\n', '.git/config', '.aws/config', '.codex/auth.json',
                     '.ssh/id_rsa', '.env', 'src/.env.local', 'src/secret.key', 'node_modules/pkg/file'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                snapshot_archive({name: ('100644', b'fixture')})
        with self.assertRaises(ValueError):
            snapshot_archive({'src': ('100644', b'file'), 'src/file': ('100644', b'child')})

    def test_modes_file_count_and_byte_bounds_are_required(self):
        for files in ({}, [], {'file': ('120000', b'/etc/passwd')}, {'file': ([], b'fixture')},
                      {'file': ('100644', 'not-bytes')}, {'file': ('100644', b'x' * (1024 * 1024 + 1))},
                      {str(index): ('100644', b'') for index in range(1001)},
                      {str(index): ('100644', b'x' * 1024 * 1024) for index in range(17)}):
            with self.subTest(kind=type(files)), self.assertRaises(ValueError):
                snapshot_archive(files)

    def test_unpinned_image_and_disabled_guard_never_start_a_container(self):
        files = {'src/file': ('100644', b'fixture')}
        with self.assertRaises(ValueError):
            ContainerWorker('/usr/bin/docker', 'busybox:latest', ('/bin/true',), files)
        worker = ContainerWorker('/usr/bin/docker', IMAGE, ('/bin/true',), files)
        with self.assertRaisesRegex(ProcessStopped, 'disabled'):
            worker.run({}, 3, lambda: False)
        self.assertIsNone(worker.last_container)
