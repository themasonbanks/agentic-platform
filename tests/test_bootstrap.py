"""Exercise adoption preflight against disposable application/template repositories."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).parents[1] / 'scripts/bootstrap_project.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app, self.template = self.root / 'app', self.root / 'template'
        for repo in (self.app, self.template):
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        for path in ('scripts/setup.sh', 'scripts/check.sh'):
            file = self.app / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('set -eu\ntrue\n')
        for path in ('AGENTS.md', '.coderabbit.yaml', 'scripts/run_ci.py', 'docs/architecture.md',
                     'docs/definition-of-done.md', '.github/pull_request_template.md'):
            file = self.template / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('fixture for ' + path + '\n')
        for repo in (self.app, self.template):
            self.git(repo, 'add', '.')
            self.git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.original_pin = bootstrap.TEMPLATE_PIN
        bootstrap.TEMPLATE_PIN = self.git(self.template, 'rev-parse', 'HEAD').decode().strip()

    def tearDown(self):
        bootstrap.TEMPLATE_PIN = self.original_pin
        self.tmp.cleanup()

    def git(self, repo, *args):
        return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)

    def test_preflight_and_generation_preserve_existing_app(self):
        before = self.git(self.app, 'status', '--porcelain')
        paths = bootstrap.prepare(self.app, self.template)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), before)
        bootstrap.prepare(self.app, self.template, True)
        self.assertTrue(all((self.app / p).is_file() for p in paths))
        workflow = (self.app / '.github/workflows/agentic-ci.yml').read_text()
        self.assertIn('@' + bootstrap.PLATFORM_PIN, workflow)
        self.assertIn('github.event_name', workflow)
        self.assertEqual((self.app / 'scripts/check.sh').read_text(), 'set -eu\ntrue\n')

    def test_dirty_tree_rejected_without_partial_write(self):
        (self.app / 'unrelated.txt').write_text('existing change')
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.app, self.template, True)
        self.assertFalse((self.app / 'AGENTS.md').exists())

    def test_ignored_existing_destination_is_not_overwritten(self):
        self.git(self.app, 'config', 'core.excludesFile', str(self.root / 'ignore'))
        (self.root / 'ignore').write_text('AGENTS.md\n')
        (self.app / 'AGENTS.md').write_text('existing rules')
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.app, self.template, True)
        self.assertEqual((self.app / 'AGENTS.md').read_text(), 'existing rules')
        self.assertFalse((self.app / '.coderabbit.yaml').exists())

    def test_symlink_ancestor_and_existing_workflow_require_manual_integration(self):
        external = self.root / 'outside'
        external.mkdir()
        (self.app / '.github').symlink_to(external, target_is_directory=True)
        self.git(self.app, 'add', '.github')
        self.git(self.app, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'link fixture')
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.app, self.template, True)
        self.assertEqual(list(external.iterdir()), [])

    def test_existing_workflow_rejected_before_files_are_written(self):
        file = self.app / '.github/workflows/existing.yml'
        file.parent.mkdir(parents=True)
        file.write_text('name: Existing\n')
        self.git(self.app, 'add', '.')
        self.git(self.app, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'workflow fixture')
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.app, self.template, True)
        self.assertFalse((self.app / 'AGENTS.md').exists())
