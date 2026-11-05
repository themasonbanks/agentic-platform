"""Verify metadata binding and fail-closed review lookup."""

import unittest
from remediation.github import GitHubReviewSource


class GitHubSourceTests(unittest.TestCase):
    def setUp(self):
        self.protected = False
        self.resolved = False
        self.pr_url = "https://api.github.com/repos/owner/app/pulls/5"
        self.found = True
        self.calls = []

    def api(self, path, data=None):
        self.calls.append((path, data))
        if path == "/graphql":
            nodes = [{"isOutdated": False, "isResolved": self.resolved,
                      "comments": {"nodes": [{"databaseId": 10}]}}] if self.found else []
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        if path.endswith("/pulls/5"):
            return {"state": "open", "draft": False, "head": {
                "ref": "fix/example", "sha": "a" * 40, "repo": {"full_name": "owner/app"}}}
        if path.endswith("/comments/10"):
            return {"id": 10, "pull_request_url": self.pr_url, "commit_id": "a" * 40,
                    "user": {"id": 123, "type": "Bot"}, "path": "src/example.py", "line": 4,
                    "body": "Boundary case needs fixing."}
        if "/branches/" in path:
            return {"protected": self.protected}
        return {"default_branch": "main"}

    def test_current_metadata_and_encoded_branch(self):
        review = GitHubReviewSource(self.api).current_review("owner/app", 5, 10)
        self.assertTrue(review.open)
        self.assertEqual(review.author_id, 123)
        self.assertTrue(any(p.endswith("fix%2Fexample") for p, _ in self.calls))

    def test_protected_branch_not_eligible(self):
        self.protected = True
        self.assertFalse(GitHubReviewSource(self.api).current_review("owner/app", 5, 10).open)

    def test_resolved_thread_propagates(self):
        self.resolved = True
        self.assertTrue(GitHubReviewSource(self.api).current_review("owner/app", 5, 10).resolved)

    def test_comment_from_other_pr_rejected(self):
        self.pr_url = "https://api.github.com/repos/owner/app/pulls/6"
        with self.assertRaises(ValueError):
            GitHubReviewSource(self.api).current_review("owner/app", 5, 10)

    def test_missing_thread_fails_closed(self):
        self.found = False
        with self.assertRaises(ValueError):
            GitHubReviewSource(self.api).current_review("owner/app", 5, 10)

    def test_repository_path_injection_rejected_before_api(self):
        with self.assertRaises(ValueError):
            GitHubReviewSource(self.api).current_review("owner/app/../../elsewhere", 5, 10)
        self.assertEqual(self.calls, [])
