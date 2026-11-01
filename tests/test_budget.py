"""Check durable cost reservations across workers and uncertain outcomes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from remediation.budget import Budget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'budget.db'
        self.db = sqlite3.connect(self.path)
        self.budget = Budget(self.db)
        self.budget.configure('2026-11', 100)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_concurrent_workers_share_one_limit(self):
        self.assertTrue(self.budget.reserve('one', '2026-11', 70))
        with sqlite3.connect(self.path) as second:
            other = Budget(second)
            self.assertFalse(other.reserve('two', '2026-11', 40))
            self.assertTrue(other.reserve('two', '2026-11', 30))

    def test_restart_retains_crashed_job_reservation(self):
        self.assertTrue(self.budget.reserve('one', '2026-11', 100))
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.budget = Budget(self.db)
        self.assertFalse(self.budget.reserve('two', '2026-11', 1))
        self.assertFalse(self.budget.reserve('one', '2026-11', 100))

    def test_missing_limit_or_price_blocks(self):
        self.assertFalse(self.budget.reserve('one', '2026-12', 1))
        for value in (None, 1.2, True, -1, 0):
            with self.assertRaises(ValueError):
                self.budget.reserve('one', '2026-11', value)

    def test_actual_cost_releases_only_unused_reservation(self):
        self.budget.reserve('one', '2026-11', 100)
        self.budget.settle('one', 40)
        self.budget.settle('one', 40)
        self.assertTrue(self.budget.reserve('two', '2026-11', 60))
        self.assertFalse(self.budget.reserve('three', '2026-11', 1))
        with self.assertRaises(ValueError):
            self.budget.settle('one', 0)

    def test_cost_overrun_is_recorded_and_blocks_more_work(self):
        self.budget.reserve('one', '2026-11', 50)
        self.budget.settle('one', 120)
        self.assertFalse(self.budget.reserve('two', '2026-11', 1))

    def test_retries_do_not_reset_budget_and_limit_cannot_be_overwritten(self):
        self.budget.reserve('attempt-1', '2026-11', 60)
        self.budget.settle('attempt-1', 60)
        self.assertFalse(self.budget.reserve('attempt-2', '2026-11', 60))
        with self.assertRaises(sqlite3.IntegrityError):
            self.budget.configure('2026-11', 1000)
