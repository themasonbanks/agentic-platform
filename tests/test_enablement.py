"""Verify explicit owner enablement, distinct dry runs and immediate revocation."""
import sqlite3
import unittest
from remediation.enablement import Enablement


class EnablementTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.gate = Enablement(self.db, 123)

    def tearDown(self):
        self.db.close()

    def approvals(self):
        for kind in ('app', 'endpoint', 'agent', 'budget'):
            self.gate.approve(123, kind, kind + '-reviewed-reference')
        for n in range(5):
            self.gate.approve(123, 'dry_run', str(n))

    def test_default_disabled_and_missing_approvals_block(self):
        self.assertFalse(self.gate.enabled())
        with self.assertRaises(PermissionError):
            self.gate.set_enabled(123, True)

    def test_duplicate_dry_runs_do_not_count(self):
        for kind in ('app', 'endpoint', 'agent', 'budget'):
            self.gate.approve(123, kind, kind)
        for _ in range(5):
            self.gate.approve(123, 'dry_run', 'same-run')
        self.assertFalse(self.gate.ready())

    def test_only_explicit_owner_switch_enables(self):
        self.approvals()
        self.assertTrue(self.gate.ready())
        self.assertFalse(self.gate.enabled())
        with self.assertRaises(PermissionError):
            self.gate.set_enabled(999, True)
        self.gate.set_enabled(123, True)
        self.assertTrue(self.gate.enabled())
        self.assertTrue(Enablement(self.db, 123).enabled())

    def test_revocation_requires_new_approvals(self):
        self.approvals()
        self.gate.set_enabled(123, True)
        self.gate.revoke(123)
        self.assertFalse(self.gate.ready())
        self.assertFalse(self.gate.enabled())

    def test_non_owner_cannot_approve(self):
        for owner in (999, True, '123'):
            with self.assertRaises(PermissionError):
                self.gate.approve(owner, 'app', 'reference')
