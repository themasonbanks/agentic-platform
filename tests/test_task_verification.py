"""Verify that task completion requires independent commit-bound evidence."""

import json
from pathlib import Path
import unittest

from task_contract.verification import Observation, identify_candidate, verify_candidate


class TaskVerificationTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).resolve().parents[1] / 'examples' / 'stockpot-task.json'
        self.task = json.loads(fixture.read_text())
        self.candidate = identify_candidate(self.task, '1' * 40)
        self.checks = [Observation(self.candidate, 'diff-check', 'passed')]

    def test_success_requires_checks_and_criterion_evidence(self):
        evidence = [Observation(self.candidate, 'reviewed-diff', 'passed', criterion='working-agreement')]
        result = verify_candidate(self.task, self.candidate, self.checks, evidence, attempts_used=1)
        self.assertEqual(result.decision, 'verified')
        self.assertEqual(result.criteria, (('working-agreement', (('reviewed-diff', 'passed'),)),))

    def test_green_checks_without_requirement_evidence_escalate(self):
        result = verify_candidate(self.task, self.candidate, self.checks, [], attempts_used=1)
        self.assertEqual(result.decision, 'escalate')
        self.assertEqual(result.criteria[0][1], (('reviewed-diff', 'missing'),))

    def test_one_criterion_cannot_satisfy_another_with_the_same_source(self):
        extra = {'id': 'rollback', 'description': 'Explain rollback.', 'evidence': ['reviewed-diff']}
        task = {**self.task, 'criteria': [*self.task['criteria'], extra]}
        candidate = identify_candidate(task, self.candidate.commit)
        checks = [Observation(candidate, 'diff-check', 'passed')]
        evidence = [Observation(candidate, 'reviewed-diff', 'passed', criterion='working-agreement')]
        result = verify_candidate(task, candidate, checks, evidence, attempts_used=1)
        self.assertEqual(result.decision, 'escalate')
        self.assertEqual(result.criteria[1], ('rollback', (('reviewed-diff', 'missing'),)))

    def test_repair_uses_fresh_evidence_and_stops_at_the_limit(self):
        failed = [Observation(self.candidate, 'reviewed-diff', 'failed', criterion='working-agreement')]
        for attempt, expected in ((1, 'repair'), (2, 'escalate')):
            with self.subTest(attempt=attempt):
                result = verify_candidate(self.task, self.candidate, self.checks, failed, attempts_used=attempt)
                self.assertEqual(result.decision, expected)

        fixed = identify_candidate(self.task, '2' * 40)
        fresh_checks = [Observation(fixed, 'diff-check', 'passed')]
        fresh_evidence = [Observation(fixed, 'reviewed-diff', 'passed', criterion='working-agreement')]
        result = verify_candidate(self.task, fixed, fresh_checks, fresh_evidence, attempts_used=2)
        self.assertEqual(result.decision, 'verified')
        with self.assertRaisesRegex(ValueError, 'different task or commit'):
            verify_candidate(self.task, fixed, self.checks, fresh_evidence, attempts_used=2)

    def test_result_cannot_change_policy_or_supply_instructions(self):
        for status in ('blocked', 'run another command'):
            with self.subTest(status=status):
                evidence = [Observation(self.candidate, 'reviewed-diff', status, criterion='working-agreement')]
                if status == 'blocked':
                    result = verify_candidate(self.task, self.candidate, self.checks, evidence, attempts_used=1)
                    self.assertEqual(result.decision, 'escalate')
                else:
                    with self.assertRaisesRegex(ValueError, 'Invalid result status'):
                        verify_candidate(self.task, self.candidate, self.checks, evidence, attempts_used=1)
