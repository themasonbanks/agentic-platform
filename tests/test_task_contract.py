"""Exercise task scope and evidence boundaries."""

import json
from pathlib import Path
import unittest

from task_contract.preflight import validate_task


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.examples = Path(__file__).resolve().parents[1] / 'examples'
        self.task = json.loads((self.examples / 'stockpot-task.json').read_text())
        self.policy = {
            'issue_repositories': {'KEB-122': 'themasonbanks/stockpot'},
            'branch': 'keb-122/working-agreement', 'worktree': 'stockpot-platform',
            'tools': {'read', 'edit', 'check', 'draft_pr'}, 'paths': ['docs'],
            'checks': {'diff-check'}, 'human_gates': {'draft-approval'},
            'evidence': {'reviewed-diff'},
            'limits': {'attempts': 2, 'runtime_seconds': 120, 'cost_microusd': 0},
        }

    def test_valid_and_unsafe_examples(self):
        validate_task(self.task, self.policy)
        with self.assertRaisesRegex(ValueError, 'Forbidden action'):
            validate_task(json.loads((self.examples / 'unsafe-task.json').read_text()), self.policy)

    def test_missing_or_untrusted_evidence_blocks_preflight(self):
        for criteria in ([], [{'id': 'example', 'description': 'Example', 'evidence': ['unknown'] }]):
            with self.subTest(criteria=criteria), self.assertRaises(ValueError):
                validate_task({**self.task, 'criteria': criteria}, self.policy)

    def test_scope_and_gate_changes_block_preflight(self):
        for change in ({'repository': 'someone/else'}, {'branch': 'different'},
                       {'paths': ['docs-other/file']}, {'paths': ['docs/../file']},
                       {'checks': ['other']}, {'human_gates': ['other']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_task({**self.task, **change}, self.policy)

    def test_invalid_budget_blocks_preflight(self):
        for cost in (True, -1, 1):
            with self.subTest(cost=cost), self.assertRaisesRegex(ValueError, 'cost_microusd'):
                validate_task({**self.task, 'limits': {**self.task['limits'], 'cost_microusd': cost}}, self.policy)
