"""Validate patch manifests and exact-head check evidence before publishing."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


@dataclass(frozen=True)
class ChangedFile:
    path: str
    operation: str
    mode: str


def validate_manifest(files, allowed_roots):
    if not files or len(files) > 20 or not allowed_roots:
        raise ValueError('Explicit bounded patch scope required')
    seen = set()
    for item in files:
        path = PurePosixPath(item.path)
        if (not item.path or path.is_absolute() or '..' in path.parts
                or '\\' in item.path or any(ord(c) < 32 for c in item.path)
                or path.as_posix() != item.path or item.path in seen):
            raise ValueError('Unsafe or duplicate path')
        seen.add(item.path)
        if item.operation not in {'add', 'modify'} or item.mode != '100644':
            raise ValueError('Only ordinary file additions/modifications are allowed')
        if len(path.parts) < 2 or path.parts[0] not in allowed_roots:
            raise ValueError('Path outside approved roots')
        if (any(part.startswith('.') for part in path.parts)
                or {'migrations', 'terraform', 'infrastructure', 'node_modules'}.intersection(path.parts)
                or re.search(r'(?i)(auth|permission|secret|credential|policy|security|lockfile)', item.path)
                or path.name in {'package.json', 'bun.lock', 'pnpm-lock.yaml', 'requirements.txt', 'Dockerfile'}):
            raise ValueError('Sensitive path requires human review')


@dataclass(frozen=True)
class CheckEvidence:
    tree_sha: str
    required: frozenset
    results: dict


def authorize_publication(expected_head, current_head, patch_tree, evidence, required_checks):
    for sha in (expected_head, current_head, patch_tree):
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Exact object IDs required')
    if expected_head != current_head:
        raise ValueError('PR head moved')
    if (not required_checks or evidence.tree_sha != patch_tree
            or set(evidence.required) != set(required_checks)
            or any(evidence.results.get(name) != 'success' for name in required_checks)):
        raise ValueError('Missing or mismatched required check evidence')
    return {'expected_head': expected_head, 'tree': patch_tree}
