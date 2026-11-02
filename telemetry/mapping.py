"""Resolve source events through explicitly verified PR and commit relationships."""
import re


class IssueResolver:
    def __init__(self, pull_requests, commits):
        self.pull_requests = dict(pull_requests)
        self.commits = dict(commits)
        for mappings, is_commit in ((self.pull_requests, False), (self.commits, True)):
            for (repository, identity), issue in mappings.items():
                valid_identity = (isinstance(identity, str) and re.fullmatch('[0-9a-f]{40}', identity)
                                  if is_commit else type(identity) is int and identity > 0)
                if (not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
                        or not valid_identity
                        or not re.fullmatch(r'[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*', issue)):
                    raise ValueError('Verified repository, identity and issue mapping required')

    def __call__(self, repository, event, payload):
        if event in {'pull_request', 'pull_request_review'}:
            source = payload['pull_request']
            numbers = [source['number']]
            sha = source['head']['sha']
        elif event == 'workflow_run':
            source = payload['workflow_run']
            numbers = [pr['number'] for pr in source.get('pull_requests', [])]
            sha = source['head_sha']
        else:
            return None
        if (len(numbers) > 1 or any(type(number) is not int or number <= 0 for number in numbers)
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)):
            return None
        pr_issue = self.pull_requests.get((repository, numbers[0])) if numbers else None
        commit_issue = self.commits.get((repository, sha))
        if pr_issue and commit_issue and pr_issue != commit_issue:
            return None
        return pr_issue or commit_issue
