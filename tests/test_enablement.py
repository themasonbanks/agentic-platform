"""Verify explicit owner enablement, distinct dry runs and immediate revocation."""
import sqlite3
import unittest
from remediation.enablement import Enablement


class EnablementTests(unittest.TestCase):
    def setUp(self):
        self.configuration = {k: k + '-reviewed-reference' for k in ('app', 'endpoint', 'agent', 'budget')}
        self.db = sqlite3.connect(':memory:')
        self.gate = Enablement(self.db, 123)

    def tearDown(self):
        self.db.close()

    def approvals(self):
        for kind in ('app', 'endpoint', 'agent', 'budget'):
            self.gate.approve(123, kind, kind + '-reviewed-reference')
        for n in range(5):
            self.gate.approve(123, 'dry_run', str(n), self.configuration)

    def test_default_disabled_and_missing_approvals_block(self):
        self.assertFalse(self.gate.enabled(self.configuration))
        with self.assertRaises(PermissionError):
            self.gate.set_enabled(123, True, self.configuration)

    def test_duplicate_dry_runs_do_not_count(self):
        for kind in ('app', 'endpoint', 'agent', 'budget'):
            self.gate.approve(123, kind, kind)
        for _ in range(5):
            self.gate.approve(123, 'dry_run', 'same-run', self.configuration)
        self.assertFalse(self.gate.ready(self.configuration))

    def test_only_explicit_owner_switch_enables(self):
        self.approvals()
        self.assertTrue(self.gate.ready(self.configuration))
        self.assertFalse(self.gate.enabled(self.configuration))
        with self.assertRaises(PermissionError):
            self.gate.set_enabled(999, True, self.configuration)
        self.gate.set_enabled(123, True, self.configuration)
        self.assertTrue(self.gate.enabled(self.configuration))
        self.assertTrue(Enablement(self.db, 123).enabled(self.configuration))

    def test_revocation_requires_new_approvals(self):
        self.approvals()
        self.gate.set_enabled(123, True, self.configuration)
        self.gate.revoke(123)
        self.assertFalse(self.gate.ready(self.configuration))
        self.assertFalse(self.gate.enabled(self.configuration))

    def test_non_owner_cannot_approve(self):
        for owner in (999, True, '123'):
            with self.assertRaises(PermissionError):
                self.gate.approve(owner, 'app', 'reference')

    def test_configuration_change_requires_new_dry_runs(self):
        self.approvals()
        self.gate.set_enabled(123, True, self.configuration)
        changed = dict(self.configuration, agent='new-agent-version')
        self.assertFalse(self.gate.enabled(changed))
        self.gate.approve(123, 'agent', changed['agent'])
        self.assertFalse(self.gate.ready(changed))
        for n in range(5):
            self.gate.approve(123, 'dry_run', 'new-' + str(n), changed)
        self.assertTrue(self.gate.ready(changed))
        self.assertFalse(self.gate.enabled(changed))

    def test_dry_run_approval_without_configuration_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.approve(123, 'dry_run', 'run-reference')
