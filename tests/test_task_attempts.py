"""Exercise verification state across process restarts and duplicate requests."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from task_contract.attempts import AttemptStore
from task_contract.verification import Observation, identify_candidate


class AttemptStoreTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.database = Path(temp) / 'attempts.db'
        self.now = 0
        fixture = Path(__file__).resolve().parents[1] / 'examples' / 'stockpot-task.json'
        self.task = json.loads(fixture.read_text())
        self.policy = {
            'issue_repositories': {'KEB-122': 'themasonbanks/stockpot'},
            'branch': 'keb-122/working-agreement', 'worktree': 'stockpot-platform',
            'tools': {'read', 'edit', 'check', 'draft_pr'}, 'paths': ['docs'],
            'checks': {'diff-check'}, 'human_gates': {'draft-approval'},
            'evidence': {'reviewed-diff'},
            'limits': {'attempts': 2, 'runtime_seconds': 120, 'cost_microusd': 0},
        }

    def record(self, store, attempt, evidence_status):
        candidate = identify_candidate(self.task, str(attempt) * 40)
        checks = [Observation(candidate, 'diff-check', 'passed')]
        evidence = [Observation(candidate, 'reviewed-diff', evidence_status, criterion='working-agreement')]
        return store.record('stockpot-1', attempt, candidate.commit, checks, evidence)

    def test_restart_keeps_reserved_attempt_and_failed_manifest(self):
        with closing(sqlite3.connect(self.database)) as db:
            store = AttemptStore(db, lambda: self.now)
            self.assertTrue(store.start('stockpot-1', self.task, self.policy))
            self.assertTrue(store.reserve('stockpot-1', 1))
            first = self.record(store, 1, 'failed')
            self.assertEqual(first['decision'], 'repair')
            self.assertTrue(store.reserve('stockpot-1', 2))
        with closing(sqlite3.connect(self.database)) as db:
            store = AttemptStore(db, lambda: self.now)
            self.assertFalse(store.start('stockpot-1', self.task, self.policy))
            self.assertFalse(store.reserve('stockpot-1', 2))
            self.assertEqual(store.current('stockpot-1'), {'attempt': 2, 'manifest': None})
            final = self.record(store, 2, 'failed')
            self.assertEqual(final['decision'], 'escalate')
            self.assertEqual(self.record(store, 2, 'failed'), final)
            with self.assertRaisesRegex(ValueError, 'different verification manifest'):
                self.record(store, 2, 'passed')
            with self.assertRaisesRegex(ValueError, 'Attempt limit'):
                store.reserve('stockpot-1', 3)

    def test_no_repair_is_reserved_until_previous_attempt_permits_it(self):
        with closing(sqlite3.connect(self.database)) as db:
            store = AttemptStore(db, lambda: self.now)
            store.start('stockpot-1', self.task, self.policy)
            store.reserve('stockpot-1', 1)
            with self.assertRaisesRegex(ValueError, 'has not authorized'):
                store.reserve('stockpot-1', 2)
            self.assertEqual(self.record(store, 1, 'passed')['decision'], 'verified')
            with self.assertRaisesRegex(ValueError, 'has not authorized'):
                store.reserve('stockpot-1', 2)

    def test_restart_does_not_extend_expiry_or_replace_task(self):
        with closing(sqlite3.connect(self.database)) as db:
            store = AttemptStore(db, lambda: self.now)
            store.start('stockpot-1', self.task, self.policy)
            store.reserve('stockpot-1', 1)
        self.now = 120_000_000_000
        with closing(sqlite3.connect(self.database)) as db:
            store = AttemptStore(db, lambda: self.now)
            self.assertFalse(store.start('stockpot-1', self.task, self.policy))
            with self.assertRaisesRegex(ValueError, 'different task snapshot'):
                store.start('stockpot-1', {**self.task, 'intent': 'Different work'}, self.policy)
            manifest = self.record(store, 1, 'passed')
            self.assertEqual(manifest['decision'], 'escalate')
            self.assertEqual(manifest['stop_reason'], 'runtime-limit')
            self.assertEqual(self.record(store, 1, 'passed'), manifest)
            with self.assertRaisesRegex(ValueError, 'time limit'):
                store.reserve('stockpot-1', 2)
