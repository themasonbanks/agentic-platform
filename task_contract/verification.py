"""Evaluate controller-collected evidence for an exact task and commit."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re


@dataclass(frozen=True)
class Candidate:
    issue: str
    repository: str
    task_digest: str
    commit: str


@dataclass(frozen=True)
class Observation:
    candidate: Candidate
    source: str
    status: str
    criterion: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class Verification:
    candidate: Candidate
    checks: tuple[tuple[str, str], ...]
    criteria: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    decision: str
    references: tuple[tuple[str | None, str, str], ...]


def identify_candidate(task, commit):
    """Identify a candidate using the approved task snapshot and full Git object ID."""
    if not isinstance(commit, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit):
        raise ValueError('A full commit ID is required')
    payload = json.dumps(task, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return Candidate(task['issue'], task['repository'], sha256(payload.encode()).hexdigest(), commit)


def verify_candidate(task, candidate, checks, evidence, *, attempts_used):
    """Evaluate structured results supplied by trusted controller adapters."""
    if identify_candidate(task, candidate.commit) != candidate:
        raise ValueError('Candidate does not match the approved task')
    if type(attempts_used) is not int or not 1 <= attempts_used <= task['limits']['attempts']:
        raise ValueError('Invalid attempt count')

    checks, evidence = tuple(checks), tuple(evidence)
    criteria = {item['id']: item['evidence'] for item in task['criteria']}
    check_status = _statuses(candidate, {(None, name) for name in task['checks']}, checks)
    evidence_status = _statuses(
        candidate, {(name, source) for name, sources in criteria.items() for source in sources}, evidence,
    )
    check_results = tuple((name, check_status.get((None, name), 'missing')) for name in task['checks'])
    criterion_results = tuple(
        (name, tuple((source, evidence_status.get((name, source), 'missing')) for source in sources))
        for name, sources in criteria.items()
    )
    statuses = {status for _, status in check_results}
    statuses.update(status for _, sources in criterion_results for _, status in sources)
    if statuses == {'passed'}:
        decision = 'verified'
    elif statuses & {'missing', 'blocked'} or attempts_used == task['limits']['attempts']:
        decision = 'escalate'
    else:
        decision = 'repair'
    references = tuple(sorted(
        ((item.criterion, item.source, item.reference)
         for item in checks + evidence if item.reference is not None),
        key=lambda item: (item[0] or '', item[1]),
    ))
    return Verification(candidate, check_results, criterion_results, decision, references)


def _statuses(candidate, expected, observations):
    result = {}
    for observation in observations:
        if observation.candidate != candidate:
            raise ValueError('Result belongs to a different task or commit')
        key = (observation.criterion, observation.source)
        if key not in expected or key in result:
            raise ValueError('Unexpected or duplicate result source')
        if observation.status not in {'passed', 'failed', 'blocked'}:
            raise ValueError('Invalid result status')
        if observation.reference is not None and not re.fullmatch(
                r'actions/runs/[1-9][0-9]{0,19}/job/[1-9][0-9]{0,19}', observation.reference):
            raise ValueError('Invalid evidence reference')
        result[key] = observation.status
    return result
