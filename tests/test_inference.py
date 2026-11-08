"""Verify per-call reservations and dispatch fencing without a model connection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from remediation.budget import Budget
from remediation.enablement import Enablement
from remediation.inference import InferenceAllowance, InferencePolicy
from remediation.intake import Intake
from remediation.supervisor import Supervisor
from test_supervisor import Source

DIGEST = hashlib.sha256(b'fixture-request-not-a-prompt').hexdigest()


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'control.db'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.now = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
        self.secret = b'fixture-only-secret-' * 3
        self.policy = InferencePolicy('fixture-model', 'fixture-price-v1', 100000, 200000,
                                      1000, 100, 3, self.now + 600)
        self.configuration = {'app': 'fixture-app', 'endpoint': 'fixture-endpoint',
                              'agent': 'fixture-agent', 'budget': self.policy.fingerprint()}
        self.supervisor = self.connect(self.db)
        self.supervisor.budget.configure('2026-11', 100)
        self.approve()
        self.run = self.start()
        self.allowance = InferenceAllowance(self.supervisor, self.policy)

    def approve(self):
        enablement = self.supervisor.enablement
        for kind, reference in self.configuration.items():
            enablement.approve(7, kind, reference)
        for index in range(5):
            enablement.approve(7, 'dry_run', 'fixture-' + str(index), self.configuration)
        enablement.set_enabled(7, True, self.configuration)

    def start(self, comment=10, pr=5):
        body = json.dumps({'action': 'created', 'repository': {'full_name': 'owner/app'},
                           'installation': {'id': 42}, 'pull_request': {'number': pr},
                           'comment': {'id': comment}}).encode()
        signature = 'sha256=' + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        self.supervisor.intake.receive('pull_request_review_comment', signature, body)
        request = self.supervisor.prepare(self.db.execute('SELECT id FROM jobs WHERE comment=?', (comment,)).fetchone()[0])
        self.supervisor.execute(request['run'], SimpleNamespace(run=lambda request, seconds, guard: None))
        return request['run']

    def connect(self, db):
        return Supervisor(Intake(db, self.secret, {'owner/app': 42}, 123, Source()), Budget(db),
                          Enablement(db, 7), self.configuration, 30, clock=lambda: self.now)

    def reserve(self, index=1, output=16):
        return self.allowance.reserve(self.run, format(index, '032x'), DIGEST, 10, output)

    def test_generate_counts_and_dispatches_identical_bytes_once(self):
        identity = format(1, '032x')
        counted = []
        def count_input(payload):
            counted.append(payload)
            return 10

        def send(payload):
            self.assertEqual(counted, [payload])
            self.assertEqual(self.db.execute('SELECT digest,status FROM inference_calls').fetchone(),
                             (hashlib.sha256(payload).hexdigest(), 'dispatched'))
            self.assertEqual(json.loads(payload)['max_output_tokens'], 16)
            return {'model': self.policy.model, 'status': 'completed',
                    'usage': {'input_tokens': 10, 'output_tokens': 5}}

        result = self.allowance.generate(self.run, identity, 'private fixture prompt', 16,
                                         count_input=count_input, send=send)
        self.assertEqual(result['status'], 'completed')
        with self.assertRaises(PermissionError):
            self.allowance.generate(self.run, identity, 'private fixture prompt', 16,
                                    count_input=count_input, send=send)
        self.assertEqual(len(counted), 1)
        self.assertNotIn('private fixture prompt', '\n'.join(self.db.iterdump()))

    def test_generate_blocks_transport_when_input_exceeds_approved_limit(self):
        with self.assertRaises(ValueError):
            self.allowance.generate(self.run, format(1, '032x'), 'fixture', 16,
                                    count_input=lambda payload: 1001,
                                    send=lambda payload: self.fail('Unexpected dispatch'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM inference_calls').fetchone()[0], 0)

    def test_generate_transport_failure_holds_attempt_and_allowance(self):
        def send(payload):
            raise TimeoutError('private fixture provider error')

        with self.assertRaisesRegex(RuntimeError, '^Provider request outcome is unknown$'):
            self.allowance.generate(self.run, format(1, '032x'), 'fixture', 16,
                                    count_input=lambda payload: 10, send=send)
        self.assertFalse(self.supervisor.can_continue(self.run))
        self.assertEqual(self.db.execute('SELECT status FROM inference_calls').fetchone(), ('unknown',))
        self.assertEqual(self.db.execute('SELECT status FROM spend_reservations').fetchone(), ('reserved',))

    def test_unique_dispatch_binds_request_and_retains_full_call_capacity(self):
        self.assertTrue(self.reserve())
        self.assertFalse(self.reserve())
        identity = format(1, '032x')
        self.assertFalse(self.allowance.dispatch(identity, 'a' * 64))
        self.assertTrue(self.allowance.dispatch(identity, DIGEST))
        self.assertFalse(self.allowance.dispatch(identity, DIGEST))
        self.assertTrue(self.allowance.complete(identity, 8, 5))
        self.assertEqual(self.db.execute('SELECT reserved,status FROM inference_calls').fetchone(), (5, 'complete'))
        self.assertEqual(self.db.execute('SELECT reserved,status FROM spend_reservations').fetchone(), (30, 'reserved'))
        self.assertEqual(self.supervisor.finish(self.run, True, 3), 'execution_complete')
        self.assertFalse(self.reserve(2))

    def test_parallel_connections_cannot_oversubscribe_one_run(self):
        def attempt(index):
            with closing(sqlite3.connect(self.path)) as db:
                allowance = InferenceAllowance(self.connect(db), self.policy)
                return allowance.reserve(self.run, format(index, '032x'), DIGEST, 10, 100)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (1, 2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.db.execute('SELECT SUM(reserved) FROM inference_calls').fetchone()[0], 21)

    def test_parallel_dispatch_has_one_winner(self):
        self.reserve()
        def dispatch(_):
            with closing(sqlite3.connect(self.path)) as db:
                return InferenceAllowance(self.connect(db), self.policy).dispatch(format(1, '032x'), DIGEST)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(dispatch, (1, 2)))
        self.assertEqual(sorted(results), [False, True])

    def test_call_count_expiry_and_owner_revocation_block_dispatch(self):
        for index in range(1, 4):
            self.assertTrue(self.reserve(index))
        self.assertFalse(self.reserve(4))
        self.supervisor.enablement.revoke(7)
        self.assertFalse(self.allowance.dispatch(format(1, '032x'), DIGEST))
        self.assertFalse(self.reserve(5))

    def test_policy_must_be_approved_and_cover_the_whole_execution_window(self):
        with self.assertRaisesRegex(ValueError, 'owner-approved'):
            InferenceAllowance(self.supervisor, replace(self.policy, output_microusd_per_million=1))
        self.now += 600
        self.assertFalse(self.reserve())

    def test_short_price_window_blocks_an_otherwise_valid_run(self):
        self.supervisor.finish(self.run, True, 0)
        self.policy = replace(self.policy, valid_until=self.now + 200)
        self.configuration['budget'] = self.policy.fingerprint()
        self.supervisor = self.connect(self.db)
        self.approve()
        self.run = self.start(11)
        self.allowance = InferenceAllowance(self.supervisor, self.policy)
        self.assertTrue(self.supervisor.can_continue(self.run))
        self.assertFalse(self.reserve())

    def test_new_approved_configuration_cannot_reuse_old_running_execution(self):
        self.policy = replace(self.policy, price_reference='fixture-price-v2')
        self.configuration['budget'] = self.policy.fingerprint()
        self.supervisor = self.connect(self.db)
        self.approve()
        self.allowance = InferenceAllowance(self.supervisor, self.policy)
        self.assertTrue(self.supervisor.enablement.enabled(self.configuration))
        self.assertFalse(self.supervisor.can_continue(self.run))
        self.assertFalse(self.reserve())

    def test_legacy_run_without_configuration_binding_stays_disabled(self):
        request = json.loads(self.db.execute('SELECT request FROM execution_runs WHERE run=?', (self.run,)).fetchone()[0])
        request.pop('configuration_digest')
        with self.db:
            self.db.execute('UPDATE execution_runs SET request=? WHERE run=?', (json.dumps(request), self.run))
        self.assertFalse(self.supervisor.can_continue(self.run))
        self.assertFalse(self.reserve())

    def test_open_call_prevents_finish_and_direct_budget_settlement(self):
        self.reserve()
        for actual in (None, 0):
            with self.subTest(actual=actual), self.assertRaisesRegex(ValueError, 'Inference calls'):
                self.supervisor.finish(self.run, True, actual)
        with self.assertRaisesRegex(ValueError, 'Inference calls'):
            self.supervisor.budget.settle(self.run, 0)
        self.assertEqual(self.db.execute('SELECT status FROM execution_runs').fetchone()[0], 'running')
        self.allowance.cancel(format(1, '032x'))
        self.assertEqual(self.supervisor.finish(self.run, True, 0), 'execution_complete')

    def test_finishing_run_cannot_accept_a_concurrent_call(self):
        settle = self.supervisor.budget.settle
        outcomes = []
        def settle_after_probe(run, actual):
            with closing(sqlite3.connect(self.path)) as db:
                other = InferenceAllowance(self.connect(db), self.policy)
                outcomes.append(other.reserve(run, format(1, '032x'), DIGEST, 10, 16))
            settle(run, actual)
        self.supervisor.budget.settle = settle_after_probe
        self.assertEqual(self.supervisor.finish(self.run, True, 0), 'execution_complete')
        self.assertEqual(outcomes, [False])

    def test_ambiguous_dispatch_keeps_lock_and_money_without_retry(self):
        self.reserve()
        identity = format(1, '032x')
        self.allowance.dispatch(identity, DIGEST)
        with self.assertRaises(ValueError):
            self.allowance.cancel(identity)
        self.allowance.uncertain(identity)
        self.assertFalse(self.allowance.dispatch(identity, DIGEST))
        self.assertFalse(self.reserve(2))
        self.assertEqual(self.db.execute('SELECT status,reason FROM execution_runs').fetchone(),
                         ('needs_human', 'inference_unknown'))
        self.assertEqual(self.db.execute('SELECT status FROM attempts').fetchone()[0], 'running')
        self.assertEqual(self.db.execute('SELECT reserved,status FROM spend_reservations').fetchone(), (30, 'reserved'))

    def test_provider_overrun_stops_further_calls(self):
        self.reserve()
        identity = format(1, '032x')
        self.allowance.dispatch(identity, DIGEST)
        self.assertFalse(self.allowance.complete(identity, 11, 17))
        self.assertEqual(self.db.execute('SELECT status FROM inference_calls').fetchone()[0], 'overrun')
        self.assertFalse(self.supervisor.can_continue(self.run))
        self.assertEqual(self.db.execute('SELECT status FROM attempts').fetchone()[0], 'running')

    def test_provider_overrun_blocks_other_runs_in_the_same_month(self):
        other = self.start(11, 6)
        self.assertTrue(self.supervisor.can_continue(other))
        self.reserve()
        identity = format(1, '032x')
        self.allowance.dispatch(identity, DIGEST)
        self.allowance.complete(identity, 11, 16)
        self.assertFalse(self.supervisor.can_continue(other))
        self.assertFalse(self.allowance.reserve(other, format(2, '032x'), DIGEST, 10, 16))
        self.assertFalse(self.supervisor.budget.reserve('new-work', '2026-11', 1))

    def test_reconciled_monthly_overspend_blocks_an_existing_execution(self):
        self.assertTrue(self.supervisor.budget.reserve('other-work', '2026-11', 1))
        self.supervisor.budget.settle('other-work', 101)
        self.assertFalse(self.supervisor.can_continue(self.run))
        self.assertFalse(self.reserve())

    def test_malformed_usage_stays_unknown_and_holds_the_run(self):
        self.reserve()
        identity = format(1, '032x')
        self.allowance.dispatch(identity, DIGEST)
        self.assertFalse(self.allowance.complete(identity, None, True))
        self.assertEqual(self.db.execute('SELECT status,input_used,output_used FROM inference_calls').fetchone(),
                         ('unknown', None, None))
        self.assertFalse(self.supervisor.can_continue(self.run))
        with self.assertRaises(ValueError):
            self.supervisor.finish(self.run, True, 0)

    def test_invalid_token_counts_cannot_obtain_allowance(self):
        for inputs, outputs in ((True, 16), (-1, 16), (1001, 16), (10, 15), (10, 101)):
            with self.subTest(inputs=inputs, outputs=outputs), self.assertRaises(ValueError):
                self.allowance.reserve(self.run, format(1, '032x'), DIGEST, inputs, outputs)
        self.assertEqual(self.db.execute('SELECT count(*) FROM inference_calls').fetchone()[0], 0)
