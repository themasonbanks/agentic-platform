"""Wire authenticated webhook routes to independent database connections."""
from contextlib import closing
import time
from telemetry.events import EventStore
from telemetry.github import receive
from telemetry.http import application
from telemetry.linear import receive_issue


def create_application(connect, github_secret, installations, resolve_issue,
                       linear_secret, organization, issues, states,
                       now_ms=lambda: time.time_ns() // 1_000_000, inline_evidence=None):
    if any(not isinstance(secret, bytes) or len(secret) < 32
           for secret in (github_secret, linear_secret)):
        raise ValueError('Webhook secrets must be explicitly configured')
    if not installations or not organization or not issues or not states or not callable(resolve_issue):
        raise ValueError('Explicit trusted source mappings required')
    if inline_evidence is not None and not callable(inline_evidence):
        raise ValueError('Trusted inline evidence adapter required')
    installations, issues, states = dict(installations), dict(issues), dict(states)

    def github(body, environ):
        with closing(connect()) as db:
            return receive(EventStore(db), environ.get('HTTP_X_GITHUB_EVENT'), body,
                           environ.get('HTTP_X_HUB_SIGNATURE_256'), github_secret,
                           installations, resolve_issue, inline_evidence)

    def linear(body, environ):
        with closing(connect()) as db:
            return receive_issue(EventStore(db), body, environ.get('HTTP_LINEAR_SIGNATURE'),
                                 linear_secret, organization, issues, states, now_ms())

    return application({'/webhooks/github': github, '/webhooks/linear': linear})
