"""Read real committed objects without running filters or copying local state."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from remediation.git_snapshot import GitContainerWorker, committed_files
from remediation.process import ProcessStopped


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = {'PATH': os.defpath, 'HOME': str(self.root), 'TMPDIR': str(self.root),
                            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git('init', '-q')
        (self.root / 'src').mkdir()
        (self.root / 'src/example.py').write_bytes(b'value = 1\n')
        (self.root / '.gitignore').write_text('.env.local\n')
        self.commit()

    def git(self, *arguments):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(self.root),
                                        *arguments], env=self.environment, stderr=subprocess.DEVNULL, timeout=10)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'source fixture')
        self.head = self.git('rev-parse', 'HEAD').decode().strip()

    def snapshot(self, guard=lambda: True):
        return committed_files(self.root, self.head, 20, guard)

    def test_committed_bytes_exclude_working_changes_ignored_state_and_git_config(self):
        (self.root / 'src/example.py').write_bytes(b'uncommitted fixture\n')
        (self.root / '.env.local').write_text('fixture-private-value')
        index = self.git('ls-files', '--stage')
        foreign_index = self.root / 'foreign-index'
        with patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_INDEX_FILE': str(foreign_index)}):
            files = self.snapshot()
        self.assertEqual(files['src/example.py'], ('100644', b'value = 1\n'))
        self.assertNotIn('.env.local', files)
        self.assertFalse(any(name.startswith('.git/') for name in files))
        self.assertNotIn(b'fixture-private-value', b''.join(item[1] for item in files.values()))
        self.assertEqual(self.git('ls-files', '--stage'), index)
        self.assertEqual((self.root / 'src/example.py').read_bytes(), b'uncommitted fixture\n')
        self.assertFalse(foreign_index.exists())

    def test_large_binary_blob_and_executable_mode_are_preserved(self):
        binary = bytes(range(256)) * 2048
        (self.root / 'src/data.bin').write_bytes(binary)
        (self.root / 'src/check.sh').write_bytes(b'#!/bin/sh\nprintf fixture\n')
        (self.root / 'src/check.sh').chmod(0o755)
        self.commit()
        files = self.snapshot()
        self.assertEqual(files['src/data.bin'], ('100644', binary))
        self.assertEqual(files['src/check.sh'][0], '100755')

    def test_replacement_commit_cannot_change_reviewed_source(self):
        original = self.head
        (self.root / 'src/example.py').write_bytes(b'unreviewed replacement\n')
        self.commit()
        replacement = self.head
        self.git('reset', '--hard', original)
        self.git('replace', original, replacement)
        self.head = original
        self.assertEqual(self.snapshot()['src/example.py'][1], b'value = 1\n')

    def test_configured_clean_filter_is_not_executed(self):
        (self.root / '.gitattributes').write_text('src/*.py filter=fixture\n')
        self.commit()
        marker = self.root.parent / (self.root.name + '-filter-ran')
        self.addCleanup(marker.unlink, missing_ok=True)
        program = 'import pathlib;pathlib.Path(' + repr(str(marker)) + ').touch()'
        self.git('config', 'filter.fixture.clean', shlex.join([sys.executable, '-c', program]))
        (self.root / 'src/example.py').write_bytes(b'dirty fixture\n')
        self.assertEqual(self.snapshot()['src/example.py'][1], b'value = 1\n')
        self.assertFalse(marker.exists())

    def test_partial_clone_wrong_head_and_revocation_fail_before_container_execution(self):
        with self.assertRaises(ValueError):
            committed_files(self.root, 'a' * 40, 20, lambda: True)
        with self.assertRaisesRegex(ProcessStopped, 'disabled'):
            self.snapshot(lambda: False)
        self.git('config', 'remote.origin.promisor', 'true')
        with self.assertRaisesRegex(ValueError, 'Partial clones'):
            self.snapshot()

    def test_symlinks_submodules_and_oversized_source_are_not_read(self):
        (self.root / 'src/link').symlink_to('/nonexistent')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'ordinary'):
            self.snapshot()
        self.git('rm', 'src/link')
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.head + ',vendor/module')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'gitlink fixture')
        self.head = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError, 'ordinary'):
            self.snapshot()
        self.git('rm', '--cached', 'vendor/module')
        (self.root / 'src/large.bin').write_bytes(b'x' * (1024 * 1024 + 1))
        self.commit()
        with self.assertRaisesRegex(ValueError, 'size/count'):
            self.snapshot()

    def test_request_scope_must_match_operator_source_binding(self):
        worker = GitContainerWorker(self.root, 'fixture/app', self.head, '/usr/bin/docker',
                                    'unconfigured-image', ('/bin/true',))
        for request in ({}, {'repository': 'other/app', 'head_sha': self.head},
                        {'repository': 'fixture/app', 'head_sha': 'a' * 40}):
            with self.assertRaisesRegex(ProcessStopped, 'scope'):
                worker.run(request, 10, lambda: True)
        self.assertIsNone(worker.last_container)
