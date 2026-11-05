"""Verify publication payloads, enablement and remote head-race refusal."""
import base64
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from remediation.enablement import Enablement
from remediation.git_patch import inspect_patch
from remediation.intake import Review
from remediation.publisher import Publisher
from remediation.publishing import CheckEvidence


class PublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)
        git('init', '-q')
        (self.root / 'src').mkdir()
        file = self.root / 'src/calculate.py'
        file.write_text('value = 1\n')
        git('add', '.')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.head = git('rev-parse', 'HEAD').decode().strip()
        file.write_text('value = 2\n')
        self.patch = git('diff', '--no-ext-diff')
        git('restore', '--', 'src/calculate.py')
        self.tree, _ = inspect_patch(self.root, self.head, self.patch, {'src'})
        self.evidence = CheckEvidence(self.tree, frozenset({'tests'}), {'tests': 'success'})
        self.review = Review(self.head, 'owner/app', 'review-fix', True, self.head, 7,
                             True, 'src/calculate.py', 1, 'Incorrect result', False, False)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.gate = Enablement(db, 42)
        self.config = dict(app='app-v1', endpoint='endpoint-v1', agent='agent-v1', budget='budget-v1')
        for kind, reference in self.config.items():
            self.gate.approve(42, kind, reference)
        for i in range(5):
            self.gate.approve(42, 'dry_run', 'fixture-' + str(i), self.config)
        self.calls = []
        self.remote_head = self.head
        owner = self
        class Source:
            def current_review(self, *args):
                return owner.review
        def api(path, payload):
            self.calls.append((path, payload))
            if payload['variables']['input']['expectedHeadOid'] != self.remote_head:
                raise ValueError('Remote branch moved')
            return {'data': {'createCommitOnBranch': {'commit': {
                'oid': 'b' * 40, 'tree': {'oid': self.tree}}}}}
        self.publisher = Publisher(api, Source(), self.gate, self.config, {'owner/app'}, 7)

    def publish(self):
        return self.publisher.publish('owner/app', 3, 9, self.head, self.root, self.patch,
                                      self.evidence, {'tests'}, {'src'})

    def test_disabled_until_owner_switch_then_exact_atomic_payload(self):
        with self.assertRaises(PermissionError):
            self.publish()
        self.assertEqual(self.calls, [])
        self.gate.set_enabled(42, True, self.config)
        self.assertEqual(self.publish()['tree'], self.tree)
        path, payload = self.calls[0]
        self.assertEqual(path, '/graphql')
        inputs = payload['variables']['input']
        self.assertEqual(inputs['expectedHeadOid'], self.head)
        self.assertEqual(inputs['branch'], {'repositoryNameWithOwner': 'owner/app', 'refName': 'review-fix'})
        self.assertEqual(base64.b64decode(inputs['fileChanges']['additions'][0]['contents']), b'value = 2\n')
        self.assertNotIn('Incorrect result', str(payload))

    def test_stale_finding_and_failed_checks_never_mutate(self):
        self.gate.set_enabled(42, True, self.config)
        self.review = replace(self.review, resolved=True)
        with self.assertRaises(ValueError):
            self.publish()
        self.review = replace(self.review, resolved=False)
        self.evidence = CheckEvidence(self.tree, frozenset({'tests'}), {'tests': 'failure'})
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_remote_race_rejected_without_retry(self):
        self.gate.set_enabled(42, True, self.config)
        self.remote_head = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'Remote branch moved'):
            self.publish()
        self.assertEqual(len(self.calls), 1)

    def test_edited_finding_during_preparation_never_mutates(self):
        self.gate.set_enabled(42, True, self.config)
        owner = self
        class Source:
            calls = 0
            def current_review(self, *args):
                self.calls += 1
                return owner.review if self.calls == 1 else replace(owner.review, body='Changed finding')
        self.publisher.source = Source()
        with self.assertRaisesRegex(ValueError, 'target changed'):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_revocation_during_final_metadata_fetch_never_mutates(self):
        self.gate.set_enabled(42, True, self.config)
        owner = self
        class Source:
            calls = 0
            def current_review(self, *args):
                self.calls += 1
                if self.calls == 2:
                    owner.gate.revoke(42)
                return owner.review
        self.publisher.source = Source()
        with self.assertRaises(PermissionError):
            self.publish()
        self.assertEqual(self.calls, [])
