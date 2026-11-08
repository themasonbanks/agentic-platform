"""Collect exact-commit evidence from controller-selected GitHub Actions jobs."""

from task_contract.verification import Observation, identify_candidate


def collect_jobs(task, commit, *, run_id, workflow_id, check_jobs, evidence_jobs, get_json):
    """Return structured observations or None while the approved run is still active."""
    candidate = identify_candidate(task, commit)
    if any(type(value) is not int or value < 1 for value in (run_id, workflow_id)):
        raise ValueError('Explicit workflow and run IDs required')
    criteria = {(item['id'], source) for item in task['criteria'] for source in item['evidence']}
    if set(check_jobs) != set(task['checks']) or not set(evidence_jobs) <= criteria:
        raise ValueError('Job mappings must match the approved task')
    path = f'/repos/{candidate.repository}/actions/runs/{run_id}'
    run = get_json(path)
    if (run['id'] != run_id or run['workflow_id'] != workflow_id
            or run['repository']['full_name'] != candidate.repository
            or (run.get('head_repository') or {}).get('full_name') != candidate.repository
            or run['head_branch'] != task['branch'] or run['head_sha'] != commit
            or run['event'] != 'push'):
        raise ValueError('Workflow run does not match the approved branch, commit and workflow')
    if run['status'] != 'completed':
        return None
    attempt = run['run_attempt']
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Explicit workflow attempt required')
    response = get_json(f'{path}/attempts/{attempt}/jobs?per_page=100')
    jobs = response['jobs']
    if len(jobs) != response['total_count'] or len(jobs) > 100:
        raise ValueError('Expected a complete bounded workflow job list')
    names = set(check_jobs.values()) | set(evidence_jobs.values())
    selected = {}
    for job in jobs:
        name = job['name']
        if name not in names:
            continue
        if (name in selected or job['run_id'] != run_id or job['head_sha'] != commit
                or job['run_attempt'] != attempt or job['status'] != 'completed'
                or type(job['id']) is not int or job['id'] < 1):
            raise ValueError('Ambiguous or stale workflow job')
        conclusion = job['conclusion']
        status = {'success': 'passed', 'failure': 'failed'}.get(conclusion, 'blocked')
        selected[name] = status, f'actions/runs/{run_id}/job/{job["id"]}'

    checks = []
    for source, name in check_jobs.items():
        if name in selected:
            status, reference = selected[name]
            checks.append(Observation(candidate, source, status, reference=reference))
    evidence = []
    for (criterion, source), name in evidence_jobs.items():
        if name in selected:
            status, reference = selected[name]
            evidence.append(Observation(candidate, source, status, criterion, reference))
    return checks, evidence
