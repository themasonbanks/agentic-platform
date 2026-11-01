"""Exercise durable execution handoffs without starting coding or paid work."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from remediation.budget import Budget
from remediation.enablement import Enablement
from remediation.intake import Intake, Review
from remediation.supervisor import Supervisor


CONFIGURATION = {'app': 'fixture-app-v1', 'endpoint': 'fixture-endpoint-v1',
                 'agent': 'fixture-agent-v1', 'budget': 'fixture-budget-v1'}


class Source:
    review = Review('a' * 40, 'owner/app', 'fix/example', True, 'a' * 40,
                    123, True, 'src/example.py', 4, 'Handle the empty result.', False, False)

    def current_review(self, repository, pr, comment):
        return self.review


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'control.db'
        self.db = sqlite3.connect(self.path)
        self.source, self.secret = Source(), b'fixture-only-secret-' * 3
        self.now = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
        self.connect()
        self.budget.configure('2026-11', 100)

    def connect(self):
        self.intake = Intake(self.db, self.secret, {'owner/app': 42}, 123, self.source)
        self.budget = Budget(self.db)
        self.enablement = Enablement(self.db, 7)
        self.supervisor = Supervisor(self.intake, self.budget, self.enablement,
                                     CONFIGURATION, 30, clock=lambda: self.now)

    def enable(self):
        for kind, reference in CONFIGURATION.items():
            self.enablement.approve(7, kind, reference)
        for index in range(5):
            self.enablement.approve(7, 'dry_run', 'fixture-' + str(index), CONFIGURATION)
        self.enablement.set_enabled(7, True, CONFIGURATION)

    def queue(self, comment=10):
        body = json.dumps({'action': 'created', 'repository': {'full_name': 'owner/app'},
                           'installation': {'id': 42}, 'pull_request': {'number': 5},
                           'comment': {'id': comment}}).encode()
        signature = 'sha256=' + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        self.assertEqual(self.intake.receive('pull_request_review_comment', signature, body), 'queued')
        return self.db.execute('SELECT id FROM jobs WHERE comment=?', (comment,)).fetchone()[0]

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def test_disabled_default_does_not_claim_or_reserve(self):
        job = self.queue()
        self.assertIsNone(self.supervisor.prepare(job))
        self.assertEqual(self.db.execute('SELECT count(*) FROM attempts').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM spend_reservations').fetchone()[0], 0)

    def test_handoff_reserves_before_return_and_dry_run_cannot_complete_it(self):
        self.enable()
        request = self.supervisor.prepare(self.queue())
        self.assertEqual((request['mode'], request['spend_limit'], request['period']), ('execution', 30, '2026-11'))
        self.assertEqual(self.db.execute('SELECT reserved,status FROM spend_reservations WHERE run=?',
                                         (request['run'],)).fetchone(), (30, 'reserved'))
        self.assertTrue(self.supervisor.can_continue(request['run']))
        with self.assertRaises(ValueError):
            self.intake.finish_dry_run(request['attempt'], self.now, True)
        self.assertEqual(self.supervisor.finish(request['run'], True, 10), 'execution_complete')
        with self.assertRaises(ValueError):
            self.supervisor.finish(request['run'], True, 10)

    def test_missing_or_exhausted_monthly_budget_requires_human(self):
        self.enable()
        self.budget.reserve('other-running-work', '2026-11', 80)
        self.assertIsNone(self.supervisor.prepare(self.queue()))
        self.assertEqual(self.db.execute('SELECT status,reason FROM jobs').fetchone(),
                         ('needs_human', 'budget_unavailable'))
        self.now = int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp())
        self.assertIsNone(self.supervisor.prepare(self.queue(11)))
        self.assertEqual(self.db.execute('SELECT count(*) FROM spend_reservations').fetchone()[0], 1)

    def test_deadline_is_cut_at_month_boundary_and_expiry_retains_actual_cost(self):
        self.enable()
        self.now = int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()) - 10
        request = self.supervisor.prepare(self.queue())
        self.assertEqual(request['deadline'], self.now + 10)
        self.now += 10
        self.assertFalse(self.supervisor.can_continue(request['run']))
        self.assertEqual(self.supervisor.finish(request['run'], True, 20), 'disabled_or_expired')
        self.assertEqual(self.db.execute('SELECT actual,status FROM spend_reservations').fetchone(), (20, 'settled'))

    def test_restart_preserves_running_lock_and_reservation(self):
        self.enable()
        request = self.supervisor.prepare(self.queue())
        job = self.queue(11)
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.connect()
        self.assertIsNone(self.supervisor.prepare(job))
        self.assertTrue(self.supervisor.can_continue(request['run']))
        self.now = request['deadline']
        self.assertIsNone(self.supervisor.prepare(job))
        self.assertEqual(self.db.execute('SELECT reserved,status FROM spend_reservations').fetchone(), (30, 'reserved'))

    def test_unknown_cost_is_not_released_or_guessed(self):
        self.enable()
        request = self.supervisor.prepare(self.queue())
        self.assertEqual(self.supervisor.finish(request['run'], True), 'unknown_cost')
        self.assertEqual(self.db.execute('SELECT actual,reserved,status FROM spend_reservations').fetchone(),
                         (None, 30, 'reserved'))
        self.assertEqual(self.db.execute('SELECT status FROM jobs').fetchone()[0], 'needs_human')

    def test_overrun_is_charged_and_blocks_new_work(self):
        self.enable()
        request = self.supervisor.prepare(self.queue())
        self.assertEqual(self.supervisor.finish(request['run'], True, 120), 'cost_overrun')
        self.assertIsNone(self.supervisor.prepare(self.queue(11)))
        self.assertEqual(self.db.execute('SELECT actual FROM spend_reservations').fetchone()[0], 120)

    def test_revocation_blocks_continuation_and_success(self):
        self.enable()
        request = self.supervisor.prepare(self.queue())
        self.enablement.revoke(7)
        self.assertFalse(self.supervisor.can_continue(request['run']))
        self.assertEqual(self.supervisor.finish(request['run'], True, 0), 'disabled_or_expired')

    def test_changed_heads_do_not_reset_attempt_limit(self):
        self.enable()
        for index in range(4):
            self.source.review = replace(self.source.review, head_sha=str(index) * 40, commit_sha=str(index) * 40)
            request = self.supervisor.prepare(self.queue(10 + index))
            if index < 3:
                self.assertIsNotNone(request)
                self.supervisor.finish(request['run'], True, 1)
            else:
                self.assertIsNone(request)
        self.assertEqual(self.db.execute('SELECT count(*) FROM attempts').fetchone()[0], 3)

    def test_legacy_attempt_schema_migrates_as_dry_run(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('''CREATE TABLE attempts(id INTEGER PRIMARY KEY,job INTEGER NOT NULL,
                repository TEXT NOT NULL,pr INTEGER NOT NULL,status TEXT NOT NULL,deadline INTEGER NOT NULL)''')
            db.execute("INSERT INTO attempts VALUES(1,1,'owner/app',5,'running',100)")
            db.commit()
            Intake(db, self.secret, {'owner/app': 42}, 123, self.source)
            self.assertEqual(db.execute('SELECT mode FROM attempts').fetchone()[0], 'dry_run')


if __name__ == '__main__':
    unittest.main()
