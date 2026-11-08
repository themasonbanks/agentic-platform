"""Exercise real Git patch application without changing the working tree/index."""
import subprocess
import tempfile
from pathlib import Path
import unittest
from remediation.git_patch import inspect_patch


class GitPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        (self.root / 'src').mkdir()
        (self.root / 'src/calculate.py').write_text('value = 1\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.head = self.git('rev-parse', 'HEAD').decode().strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.root), *arguments], stderr=subprocess.DEVNULL)

    def patch(self, path, text):
        file = self.root / path
        file.write_text(text)
        patch = self.git('diff', '--no-ext-diff')
        self.git('restore', '--', path)
        return patch

    def test_real_tree_and_manifest_without_worktree_change(self):
        before = self.git('status', '--porcelain')
        tree, files = inspect_patch(self.root, self.head, self.patch('src/calculate.py', 'value = 2\n'), {'src'})
        self.assertEqual(files[0].path, 'src/calculate.py')
        self.assertEqual(self.git('show', tree + ':src/calculate.py'), b'value = 2\n')
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual((self.root / 'src/calculate.py').read_text(), 'value = 1\n')

    def test_invalid_patch_or_wrong_head_rejected(self):
        with self.assertRaises(ValueError):
            inspect_patch(self.root, self.head, b'not a patch', {'src'})
        with self.assertRaises(ValueError):
            inspect_patch(self.root, 'a' * 40, self.patch('src/calculate.py', 'value = 2\n'), {'src'})

    def test_sensitive_resulting_path_rejected(self):
        (self.root / 'src/auth.py').write_text('value = 1\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'auth fixture')
        head = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaises(ValueError):
            inspect_patch(self.root, head, self.patch('src/auth.py', 'value = 2\n'), {'src'})

    def test_delete_rejected(self):
        (self.root / 'src/calculate.py').unlink()
        patch = self.git('diff', '--no-ext-diff')
        self.git('restore', '--', 'src/calculate.py')
        with self.assertRaises(ValueError):
            inspect_patch(self.root, self.head, patch, {'src'})

    def test_executable_to_regular_mode_change_rejected(self):
        self.git('update-index', '--chmod=+x', 'src/calculate.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'executable fixture')
        head = self.git('rev-parse', 'HEAD').decode().strip()
        patch = self.git('-c', 'core.fileMode=true', 'diff', '--no-ext-diff')
        self.assertIn(b'old mode 100755', patch)
        self.assertIn(b'new mode 100644', patch)
        before = self.git('status', '--porcelain')
        with self.assertRaisesRegex(ValueError, 'mode changes'):
            inspect_patch(self.root, head, patch, {'src'})
        self.assertEqual(self.git('status', '--porcelain'), before)
