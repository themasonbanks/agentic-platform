"""Read bounded committed blobs into the isolated worker's source snapshot."""
from pathlib import Path
import os
import re
import shutil
import time

from remediation.container import ContainerWorker, snapshot_archive
from remediation.process import ProcessStopped, ProcessWorker


def committed_files(repository, expected_head, seconds, keep_going, *, tree=None):
    """Read raw blobs from an approved commit or a derived patch tree."""
    if (not isinstance(expected_head, str) or not re.fullmatch('[0-9a-f]{40}', expected_head)
            or type(seconds) not in (int, float) or not 0 < seconds <= 300 or not callable(keep_going)):
        raise ValueError('Exact approved commit, bounded time and fresh guard required')
    if tree is not None and (not isinstance(tree, str) or not re.fullmatch('[0-9a-f]{40}', tree)):
        raise ValueError('Exact patch tree required')
    repository = Path(repository).resolve(strict=True)
    binary = shutil.which('git', path=os.defpath)
    if binary is None or not repository.is_dir():
        raise ValueError('Trusted Git binary and checkout required')
    deadline = time.monotonic() + seconds
    def git(*arguments, output_limit=262144):
        remaining = min(15, deadline - time.monotonic())
        if remaining <= 0:
            raise ProcessStopped('Source snapshot deadline reached')
        return ProcessWorker((binary, '--no-replace-objects', '-c', 'protocol.allow=never',
                              '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
                              '-C', str(repository), *arguments), repository).run_bytes(
                                  b'', remaining, keep_going, output_limit=output_limit).output
    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != repository:
        raise ValueError('Source checkout must be the repository root')
    if git('rev-parse', 'HEAD').decode().strip() != expected_head:
        raise ValueError('Source checkout head differs from approved head')
    names = git('config', '--includes', '--list', '--name-only', '--null').decode().split('\0')
    if any(name.lower() == 'extensions.partialclone'
           or re.fullmatch(r'remote\..*\.promisor', name.lower()) for name in names):
        raise ValueError('Partial clones require manual integration')
    if tree is not None and git('cat-file', '-t', tree).strip() != b'tree':
        raise ValueError('Patch object must be a tree')
    entries = git('ls-tree', '-r', '-l', '-z', '--full-tree', tree or expected_head, '--').split(b'\0')
    metadata = {}
    total = 0
    for entry in entries:
        if not entry:
            continue
        header, separator, raw_path = entry.partition(b'\t')
        fields = header.decode('ascii').split()
        if (not separator or len(fields) != 4 or fields[0] not in {'100644', '100755'}
                or fields[1] != 'blob' or not re.fullmatch('[0-9a-f]{40}', fields[2])
                or not fields[3].isdigit()):
            raise ValueError('Only ordinary committed files are supported')
        name, size = raw_path.decode('utf-8'), int(fields[3])
        if name in metadata or size > 1024 * 1024 or len(metadata) >= 1000:
            raise ValueError('Source file size/count exceeds bound')
        total += size
        if total > 16 * 1024 * 1024:
            raise ValueError('Source snapshot exceeds total bound')
        metadata[name] = (fields[0], fields[2], size)
    snapshot_archive({name: (mode, b'') for name, (mode, _, _) in metadata.items()})
    files = {}
    for name, (mode, identity, size) in metadata.items():
        data = git('cat-file', 'blob', identity, output_limit=1024 * 1024)
        if len(data) != size:
            raise ValueError('Committed blob size changed during source capture')
        files[name] = (mode, data)
    if git('rev-parse', 'HEAD').decode().strip() != expected_head:
        raise ValueError('Source checkout head moved during capture')
    if not keep_going() or time.monotonic() >= deadline:
        raise ProcessStopped('Source snapshot disabled or expired')
    return files


class GitContainerWorker:
    def __init__(self, repository, repository_name, expected_head, docker_binary, image, command):
        if (not isinstance(repository_name, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository_name)
                or not isinstance(expected_head, str) or not re.fullmatch('[0-9a-f]{40}', expected_head)):
            raise ValueError('Explicit approved repository and commit required')
        self.repository, self.repository_name, self.expected_head = repository, repository_name, expected_head
        self.docker_binary, self.image, self.command = docker_binary, image, command
        self.worker = None

    @property
    def last_container(self):
        return self.worker.last_container if self.worker else None

    def run(self, request, seconds, keep_going):
        """Bind request scope, capture committed source and execute within one deadline."""
        if (not isinstance(request, dict) or request.get('repository') != self.repository_name
                or request.get('head_sha') != self.expected_head):
            raise ProcessStopped('Worker request differs from approved source scope')
        if type(seconds) not in (int, float) or not 0 < seconds <= 300 or not callable(keep_going):
            raise ValueError('Bounded runtime and controller guard required')
        deadline = time.monotonic() + seconds
        try:
            files = committed_files(self.repository, self.expected_head, seconds, keep_going)
            if time.monotonic() >= deadline or not keep_going():
                raise ProcessStopped('Execution disabled after source capture')
            self.worker = ContainerWorker(self.docker_binary, self.image, self.command, files)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessStopped('Execution expired after source capture')
            return self.worker.run(request, remaining, keep_going)
        except (OSError, ValueError) as error:
            raise ProcessStopped('Approved source capture failed') from error
