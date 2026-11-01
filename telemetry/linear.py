"""Verify Linear issue deliveries before persisting mapped state metadata."""
import hashlib
import hmac
import json
from telemetry.sources import timestamp_ns


def receive_issue(store, body, signature, secret, organization, issues, states, now_ms):
    if not isinstance(secret, bytes) or len(secret) < 32 or not isinstance(organization, str) or not organization:
        raise ValueError('Explicit secret and organization required')
    if not isinstance(body, bytes) or not 0 < len(body) <= 1_048_576:
        raise ValueError('Bounded raw delivery required')
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise ValueError('Invalid Linear signature')
    payload = json.loads(body)
    timestamp = payload['webhookTimestamp']
    if type(now_ms) is not int or type(timestamp) is not int or abs(now_ms - timestamp) > 60_000:
        raise ValueError('Stale or invalid delivery timestamp')
    if payload.get('organizationId') != organization:
        raise ValueError('Unapproved organization')
    if payload.get('type') != 'Issue' or payload.get('action') not in {'create', 'update'}:
        return False
    data = payload['data']
    if data['id'] not in issues:
        raise ValueError('Unmapped issue')
    repository, identifier = issues[data['id']]
    state = states.get(data['stateId'])
    if state not in {'backlog', 'todo', 'in_progress', 'done', 'cancelled'}:
        raise ValueError('Unmapped state')
    updated = data['updatedAt']
    event_id = hashlib.sha256(('\0'.join((data['id'], updated, data['stateId'], payload['action']))).encode()).hexdigest()
    return store.append('linear', event_id, repository, identifier, state, timestamp_ns(updated), {})
