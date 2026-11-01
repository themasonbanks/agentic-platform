"""Append verified ordinary-file patches with an atomic expected-head mutation."""
import base64
import os
from pathlib import Path
import re
import subprocess
import tempfile
from remediation.git_patch import inspect_patch
from remediation.intake import disposition
from remediation.publishing import authorize_publication


CREATE_COMMIT = '''mutation($input:CreateCommitOnBranchInput!) {
  createCommitOnBranch(input:$input) { commit { oid tree { oid } } }
}'''


class Publisher:
    def __init__(self, api, source, enablement, configuration, repositories, bot_id):
        if not repositories or type(bot_id) is not int or bot_id <= 0:
            raise ValueError('Explicit publication scope required')
        self.api, self.source, self.enablement = api, source, enablement
        self.configuration, self.repositories = dict(configuration), frozenset(repositories)
        self.bot_id = bot_id

    def _review(self, repository, pr, comment, expected_head):
        if repository not in self.repositories or not self.enablement.enabled(self.configuration):
            raise PermissionError('Publication is not enabled for this configuration/repository')
        review = self.source.current_review(repository, pr, comment)
        if (not review.open or review.outdated or review.resolved
                or review.head_repository != repository or review.head_sha != expected_head
                or review.commit_sha != expected_head or review.author_id != self.bot_id
                or not review.author_is_bot or review.branch in {'main', 'master'}
                or disposition(review) != 'candidate'):
            raise ValueError('Current approved finding and unchanged PR head required')
        return review

    def publish(self, repository, pr, comment, expected_head, workspace, patch,
                evidence, required_checks, allowed_roots):
        review = self._review(repository, pr, comment, expected_head)
        tree, files = inspect_patch(workspace, expected_head, patch, allowed_roots)
        authorize_publication(expected_head, review.head_sha, tree, evidence, required_checks)
        additions, total = [], 0
        with tempfile.TemporaryDirectory(prefix='publication-read-') as temporary:
            environment = {'PATH': os.defpath, 'HOME': temporary,
                           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                           'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
                           'GIT_TERMINAL_PROMPT': '0'}
            def git(*args):
                result = subprocess.run(['git', '-c', 'protocol.allow=never',
                                         '-C', str(Path(workspace).resolve(strict=True)), *args],
                                        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=15, check=False)
                if result.returncode:
                    raise ValueError('Verified patch object unavailable')
                return result.stdout
            for file in files:
                identity = tree + ':' + file.path
                size = int(git('cat-file', '-s', identity))
                total += size
                if size < 0 or total > 262144:
                    raise ValueError('Publication contents exceed bounded scope')
                contents = git('cat-file', 'blob', identity)
                if len(contents) != size:
                    raise ValueError('Patch blob size mismatch')
                additions.append({'path': file.path,
                                  'contents': base64.b64encode(contents).decode('ascii')})
        latest = self._review(repository, pr, comment, expected_head)
        if latest != review:
            raise ValueError('Review target changed during preparation')
        if not self.enablement.enabled(self.configuration):
            raise PermissionError('Publication enablement was revoked')
        response = self.api('/graphql', {'query': CREATE_COMMIT, 'variables': {'input': {
            'branch': {'repositoryNameWithOwner': repository, 'refName': review.branch},
            'expectedHeadOid': expected_head,
            'message': {'headline': 'Apply verified review correction'},
            'fileChanges': {'additions': additions}}}})
        commit = response['data']['createCommitOnBranch']['commit']
        if (commit['tree']['oid'] != tree or not isinstance(commit['oid'], str)
                or not re.fullmatch('[0-9a-f]{40}', commit['oid'])):
            raise ValueError('Publication result requires human reconciliation')
        return {'commit': commit['oid'], 'tree': tree}
