"""Verify issue attribution uses trusted relationships and rejects ambiguity."""
import unittest
from telemetry.mapping import IssueResolver


class MappingTests(unittest.TestCase):
    def test_pr_and_commit_resolution_ignore_untrusted_text(self):
        resolver = IssueResolver({('owner/app', 3): 'KEB-130'},
                                 {('owner/app', 'a' * 40): 'KEB-130'})
        payload = {'pull_request': {'number': 3, 'head': {'sha': 'a' * 40},
                                    'title': 'KEB-999', 'body': 'KEB-999'}}
        self.assertEqual(resolver('owner/app', 'pull_request', payload), 'KEB-130')
        self.assertIsNone(resolver('other/app', 'pull_request', payload))
        payload['pull_request']['number'] = 4
        payload['pull_request']['head']['sha'] = 'b' * 40
        self.assertIsNone(resolver('owner/app', 'pull_request', payload))

    def test_workflow_commit_mapping_and_conflicts(self):
        resolver = IssueResolver({('owner/app', 3): 'KEB-131'},
                                 {('owner/app', 'a' * 40): 'KEB-130'})
        payload = {'workflow_run': {'head_sha': 'a' * 40, 'pull_requests': []}}
        self.assertEqual(resolver('owner/app', 'workflow_run', payload), 'KEB-130')
        payload['workflow_run']['pull_requests'] = [{'number': 3}]
        self.assertIsNone(resolver('owner/app', 'workflow_run', payload))
        payload['workflow_run']['pull_requests'] = [{'number': 3}, {'number': 4}]
        self.assertIsNone(resolver('owner/app', 'workflow_run', payload))

    def test_configuration_is_snapshotted_and_validated(self):
        mappings = {('owner/app', 3): 'KEB-130'}
        resolver = IssueResolver(mappings, {})
        mappings.clear()
        payload = {'pull_request': {'number': 3, 'head': {'sha': 'a' * 40}}}
        self.assertEqual(resolver('owner/app', 'pull_request', payload), 'KEB-130')
        for prs, commits in [({('owner/app', True): 'KEB-130'}, {}),
                             ({}, {('owner/app', 'main'): 'KEB-130'}),
                             ({('owner/app', 3): 'unverified'}, {})]:
            with self.assertRaises(ValueError):
                IssueResolver(prs, commits)
