"""Validate task declarations against a controller-owned execution policy."""

from pathlib import PurePosixPath
import re


TOOLS = frozenset({'read', 'edit', 'check', 'draft_pr'})
OUTCOMES = frozenset({'verified', 'blocked', 'cancelled', 'failed'})
FIELDS = frozenset({
    'version', 'issue', 'repository', 'branch', 'worktree', 'intent', 'criteria',
    'paths', 'tools', 'checks', 'limits', 'human_gates', 'terminal_outcomes',
})
LIMITS = {'attempts': 1, 'runtime_seconds': 1, 'cost_microusd': 0}


def _names(values, field):
    if (not isinstance(values, list) or not 1 <= len(values) <= 40
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value)
                   for value in values)
            or len(set(values)) != len(values)):
        raise ValueError(f'{field} must contain unique approved identifiers')
    return set(values)


def _path(value):
    if not isinstance(value, str) or len(value) > 512 or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', value):
        raise ValueError('Paths must be canonical relative paths')
    path = PurePosixPath(value)
    if path.as_posix() != value or any(part.startswith('.') for part in value.split('/')):
        raise ValueError('Hidden and traversal paths require manual review')
    return path


def validate_task(task, policy):
    """Check a task against explicit controller bindings and return no permissions."""
    if not isinstance(task, dict) or set(task) != FIELDS or type(task['version']) is not int or task['version'] != 1:
        raise ValueError('Exact version 1 task fields required')
    issue = task['issue']
    if not isinstance(issue, str) or len(issue) > 40 or not re.fullmatch(r'[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*', issue):
        raise ValueError('Explicit Linear issue identifier required')
    repository = task['repository']
    if (not isinstance(repository, str) or len(repository) > 200
            or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
            or policy['issue_repositories'].get(issue) != repository):
        raise ValueError('Unknown issue-to-repository mapping')
    if (not isinstance(task['branch'], str) or not isinstance(task['worktree'], str)
            or task['branch'] != policy['branch'] or task['worktree'] != policy['worktree']
            or not 1 <= len(task['branch']) <= 200 or not 1 <= len(task['worktree']) <= 200
            or task['branch'] in {'main', 'master', 'refs/heads/main', 'refs/heads/master'}):
        raise ValueError('Approved non-main branch and worktree required')
    if not isinstance(task['intent'], str) or not task['intent'].strip() or len(task['intent']) > 2000:
        raise ValueError('Bounded change intent required')

    tools = _names(task['tools'], 'Tools')
    if not tools <= TOOLS or not tools <= set(policy['tools']):
        raise ValueError('Forbidden action in task tools')
    checks = _names(task['checks'], 'Checks')
    if checks != set(policy['checks']):
        raise ValueError('Required controller check references must match')
    if _names(task['human_gates'], 'Human gates') != set(policy['human_gates']):
        raise ValueError('Required human gates must match')
    if _names(task['terminal_outcomes'], 'Terminal outcomes') != OUTCOMES:
        raise ValueError('Terminal outcomes must match the controller contract')

    paths = task['paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 40:
        raise ValueError('Explicit bounded paths required')
    approved = tuple(_path(root) for root in policy['paths'])
    scoped = [_path(value) for value in paths]
    if len(set(scoped)) != len(scoped) or any(
            not any(path == root or root in path.parents for root in approved) for path in scoped):
        raise ValueError('Task paths exceed approved scope')

    criteria = task['criteria']
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 20:
        raise ValueError('Explicit acceptance criteria required')
    ids = []
    for item in criteria:
        if (not isinstance(item, dict) or set(item) != {'id', 'description', 'evidence'}
                or not isinstance(item['description'], str) or not item['description'].strip()
                or len(item['description']) > 1000):
            raise ValueError('Each criterion requires an identifier, description and evidence')
        ids.append(item['id'])
        if not _names(item['evidence'], 'Criterion evidence') <= set(policy['evidence']):
            raise ValueError('Unknown evidence source')
    _names(ids, 'Criterion identifiers')

    limits = task['limits']
    if not isinstance(limits, dict) or set(limits) != set(LIMITS):
        raise ValueError('Explicit retry, runtime and budget limits required')
    for name, minimum in LIMITS.items():
        value = limits[name]
        if type(value) is not int or not minimum <= value <= policy['limits'][name]:
            raise ValueError(f'Invalid {name} limit')
