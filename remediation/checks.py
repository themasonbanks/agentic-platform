"""Run controller-selected checks against a validated patch tree."""
import time

from remediation.container import ContainerWorker
from remediation.git_patch import inspect_patch
from remediation.git_snapshot import committed_files
from remediation.process import ProcessStopped
from remediation.publishing import CheckEvidence


def check_patch(repository, head, patch, allowed_roots, checks, docker_binary, image, seconds, keep_going):
    """Issue evidence only after each required check exits successfully."""
    if (not isinstance(checks, dict) or not 1 <= len(checks) <= 10
            or any(not isinstance(name, str) or not 1 <= len(name) <= 100 for name in checks)
            or type(seconds) not in (int, float) or not 0 < seconds <= 300
            or not callable(keep_going)):
        raise ValueError('Explicit check commands, time limit and execution guard required')
    checks = dict(checks)
    deadline = time.monotonic() + seconds

    def remaining():
        seconds_left = deadline - time.monotonic()
        if seconds_left <= 0 or not keep_going():
            raise ProcessStopped('Patch checks expired or were revoked')
        return seconds_left

    remaining()
    tree, _ = inspect_patch(repository, head, patch, allowed_roots)
    files = committed_files(repository, head, remaining(), keep_going, tree=tree)
    results = {}
    for name, command in checks.items():
        worker = ContainerWorker(docker_binary, image, command, files)
        worker.run({}, remaining(), keep_going)
        results[name] = 'success'
    remaining()
    return CheckEvidence(tree, frozenset(checks), results)
