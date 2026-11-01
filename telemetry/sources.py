"""Normalize source metadata without retaining provider payloads or text."""
from datetime import datetime
import hashlib
import json
import re


def timestamp_ns(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone-aware source timestamp required')
    return int(parsed.timestamp() * 1_000_000_000)


def workflow_run(store, payload, issue):
    run = payload['workflow_run']
    status = run['status']
    if status == 'completed':
        kind = {'success': 'success', 'failure': 'failure', 'cancelled': 'cancelled',
                'timed_out': 'failure', 'action_required': 'failure',
                'skipped': 'skipped', 'neutral': 'neutral'}.get(run['conclusion'], 'unknown')
    else:
        kind = 'running' if status == 'in_progress' else 'queued'
    attributes = {'run_id': str(run['id']), 'sha': run['head_sha'],
                  'retry_count': run['run_attempt'] - 1}
    prs = run.get('pull_requests', [])
    if len(prs) == 1:
        attributes['pr'] = prs[0]['number']
    return store.append('ci', str(run['id']) + ':' + str(run['run_attempt']) + ':' + status,
                        payload['repository']['full_name'], issue, kind,
                        timestamp_ns(run['updated_at']), attributes)


def pull_request(store, payload, issue, delivery_id):
    pr = payload['pull_request']
    if payload['action'] not in {'opened', 'reopened', 'closed'}:
        return False
    kind = 'merged' if pr['merged'] else ('closed' if pr['state'] == 'closed' else 'opened')
    return store.append('github', delivery_id, payload['repository']['full_name'], issue, kind,
                        timestamp_ns(pr['updated_at']), {'pr': pr['number'], 'sha': pr['head']['sha']})


def codex_turn(store, event, event_id, repository, issue, run_id, sha, observed_ns):
    if event.get('type') not in {'turn.completed', 'turn.failed'}:
        return False
    attributes = {'run_id': run_id, 'sha': sha}
    if event['type'] == 'turn.completed':
        usage = event.get('usage', {})
        for field in ('input_tokens', 'output_tokens'):
            if field in usage:
                attributes[field] = usage[field]
    return store.append('agent', event_id, repository, issue,
                        'success' if event['type'] == 'turn.completed' else 'failure',
                        observed_ns, attributes)


def pull_request_review(store, payload, issue, inline_evidence=None):
    if payload['action'] != 'submitted':
        return False
    review, pr = payload['review'], payload['pull_request']
    state, identity = review['state'], review['id']
    if (not isinstance(state, str) or state not in {'approved', 'changes_requested', 'commented'}
            or type(identity) is not int or not 0 < identity <= 9_223_372_036_854_775_807
            or type(pr['number']) is not int or not 0 < pr['number'] <= 9_223_372_036_854_775_807
            or not isinstance(review.get('commit_id'), str) or not re.fullmatch('[0-9a-f]{40}', review['commit_id'])
            or not isinstance(review.get('submitted_at'), str)):
        raise ValueError('Submitted review identity and state required')
    observed_ns = timestamp_ns(review['submitted_at'])
    if not 0 <= observed_ns <= 9_223_372_036_854_775_807:
        raise ValueError('Bounded submitted-review timestamp required')
    repository = payload['repository']['full_name']
    key = hashlib.sha256(json.dumps([repository, pr['number'], identity]).encode()).hexdigest()
    event_id = 'review:' + key
    existing = store.db.execute('SELECT payload FROM engineering_events WHERE source=? AND event_id=?',
                                ('github', event_id)).fetchone()
    if existing:
        classification = json.loads(existing[0])['attributes']['review_classification']
    elif state in {'approved', 'changes_requested'}:
        classification = 'decision'
    else:
        evidence = inline_evidence(repository, pr['number'], identity) if inline_evidence is not None else False
        if type(evidence) is not bool:
            raise ValueError('Explicit trusted inline evidence required')
        classification = 'inline' if evidence else 'unclassified'
    return store.append('github', event_id, repository, issue, 'reviewed',
                        observed_ns,
                        {'pr': pr['number'], 'sha': review['commit_id'], 'review_id': identity,
                         'review_state': state, 'review_classification': classification})
