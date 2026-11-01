"""Derive a bounded patch manifest with an isolated Git index."""

import os
from pathlib import Path
import subprocess
import tempfile

from remediation.publishing import ChangedFile, validate_manifest


def inspect_patch(repository, expected_head, patch, allowed_roots):
    if not isinstance(patch, bytes) or not patch or len(patch) > 262144:
        raise ValueError('Patch must be nonempty and at most 256 KiB')
    repository = Path(repository).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='review-index-') as temporary:
        environment = {
            'PATH': os.defpath,
            'HOME': temporary,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_INDEX_FILE': str(Path(temporary) / 'index'),
            'GIT_TERMINAL_PROMPT': '0',
            'GIT_NO_REPLACE_OBJECTS': '1',
        }

        def git(*arguments, data=None):
            result = subprocess.run(
                ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                 '-c', 'core.attributesFile=/dev/null', '-C', str(repository), *arguments],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment, timeout=15, check=False,
            )
            if result.returncode:
                raise ValueError('Git patch inspection failed')
            return result.stdout

        from re import fullmatch
        if not isinstance(expected_head, str) or not fullmatch('[0-9a-f]{40}', expected_head):
            raise ValueError('Exact reviewed head required')
        if git('rev-parse', 'HEAD').decode().strip() != expected_head:
            raise ValueError('Workspace head moved')
        git('read-tree', expected_head)
        git('apply', '--cached', '--whitespace=error-all', '-', data=patch)
        tree = git('write-tree').decode().strip()
        raw = git('diff', '--raw', '-z', '--no-renames', '--no-ext-diff', expected_head, tree, '--')
        fields = raw.split(b'\0')
        files = []
        for offset in range(0, len(fields) - 1, 2):
            header = fields[offset].decode('ascii').split()
            path = fields[offset + 1].decode('utf-8', errors='strict')
            operation = {'A': 'add', 'M': 'modify'}.get(header[4], 'unsupported')
            files.append(ChangedFile(path, operation, header[1]))
        validate_manifest(files, allowed_roots)
        if git('rev-parse', 'HEAD').decode().strip() != expected_head:
            raise ValueError('Workspace head moved during inspection')
        return tree, files
