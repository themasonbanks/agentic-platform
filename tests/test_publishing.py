"""Reject unsafe patch manifests and stale/missing check evidence."""
import unittest
from remediation.publishing import ChangedFile, CheckEvidence, validate_manifest, authorize_publication


class PublishingTests(unittest.TestCase):
    def test_small_ordinary_source_change_allowed(self):
        validate_manifest([ChangedFile('src/calculate.py', 'modify', '100644')], {'src'})

    def test_unsafe_sensitive_and_unapproved_paths_rejected(self):
        for path in ('../src/code.py', '/src/code.py', 'src/../code.py', 'src//code.py',
                     'src/auth.py', 'src/.env', '.github/workflows/ci.yml',
                     'src/migrations/001.sql', 'docs/change.md', 'src/package.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_manifest([ChangedFile(path, 'modify', '100644')], {'src'})

    def test_link_delete_and_duplicate_rejected(self):
        for operation, mode in [('delete', '100644'), ('modify', '120000'), ('modify', '100755')]:
            with self.assertRaises(ValueError):
                validate_manifest([ChangedFile('src/code.py', operation, mode)], {'src'})
        file = ChangedFile('src/code.py', 'modify', '100644')
        with self.assertRaises(ValueError):
            validate_manifest([file, file], {'src'})

    def test_head_and_required_checks_bind_to_patch_tree(self):
        checks = {'unit', 'lint'}
        evidence = CheckEvidence('b' * 40, frozenset(checks), {'unit': 'success', 'lint': 'success'})
        self.assertEqual(authorize_publication('a' * 40, 'a' * 40, 'b' * 40, evidence, checks)['expected_head'], 'a' * 40)
        for current, tree, required in [('c' * 40, 'b' * 40, checks),
                                        ('a' * 40, 'c' * 40, checks),
                                        ('a' * 40, 'b' * 40, {'unit', 'lint', 'integration'}),
                                        ('a' * 40, 'b' * 40, set())]:
            with self.assertRaises(ValueError):
                authorize_publication('a' * 40, current, tree, evidence, required)

    def test_failed_or_absent_checks_block(self):
        for results in ({'unit': 'failure'}, {'unit': 'skipped'}, {}):
            with self.assertRaises(ValueError):
                authorize_publication('a' * 40, 'a' * 40, 'b' * 40,
                                      CheckEvidence('b' * 40, frozenset({'unit'}), results), {'unit'})
