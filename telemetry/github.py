"""Authenticate approved GitHub deliveries before normalizing telemetry."""
import hashlib
import hmac
import json
from telemetry.sources import pull_request, workflow_run


def receive(store, event, body, signature, secret, installations, resolve_issue):
    if not isinstance(secret, bytes) or len(secret) < 32 or not installations:
        raise ValueError('Explicit webhook secret and installation allowlist required')
    if not isinstance(body, bytes) or not 0 < len(body) <= 1_048_576:
        raise ValueError('Bounded raw delivery required')
    expected = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise ValueError('Invalid GitHub signature')
    if event not in {'workflow_run', 'pull_request'}:
        return False
    payload = json.loads(body)
    repository = payload['repository']['full_name']
    installation = payload.get('installation', {}).get('id')
    if repository not in installations or type(installation) is not int or installation != installations[repository]:
        raise ValueError('Unapproved repository installation')
    issue = resolve_issue(repository, event, payload)
    if not issue:
        raise ValueError('Verified issue mapping required')
    if event == 'workflow_run':
        return workflow_run(store, payload, issue)
    pr = payload['pull_request']
    identity = [repository, payload['action'], pr['number'], pr['updated_at'],
                pr['head']['sha'], pr['state'], pr['merged']]
    delivery_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return pull_request(store, payload, issue, delivery_key)

