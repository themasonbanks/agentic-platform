"""Seed authenticated synthetic deliveries through the real telemetry producer."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.events import EventStore
from telemetry.mapping import IssueResolver
from telemetry.metrics import render_metrics
from telemetry.metrics_http import create_metrics_application
from telemetry.reviews import InlineReviewEvidence
from telemetry.service import create_application
from telemetry.sources import codex_turn


def produce(directory, start, end):
    database = directory / 'producer.db'
    with database.open('xb'):
        pass
    database.chmod(0o600)
    with closing(sqlite3.connect(database)) as db:
        EventStore(db)
    repositories = ('fixture/alpha', 'fixture/beta')
    secret, token = b'fixture-only-webhook-key-' * 2, 'fixture-only-metrics-token-' * 2
    marker = 'fixture-private-text-must-not-persist'
    clock, evidence, lines = [start], {}, []
    def connect():
        return sqlite3.connect(database)
    def inline_api(route):
        identity = int(route.split('/')[-2])
        return evidence.get(identity, [])
    app = create_application(connect, secret, dict.fromkeys(repositories, 42),
                             IssueResolver({(repo, index + 1): 'KEB-' + str(131 + index)
                                            for index, repo in enumerate(repositories)}, {}),
                             secret, 'fixture-org',
                             {'issue-' + str(index): (repo, 'KEB-' + str(131 + index))
                              for index, repo in enumerate(repositories)}, {'done': 'done'},
                             now_ms=lambda: clock[0] * 1000, inline_evidence=InlineReviewEvidence(inline_api))
    metrics = create_metrics_application(connect, token, repositories)
    def invoke(target, environ, expected='200 OK'):
        statuses = []
        body = b''.join(target(environ, lambda status, headers: statuses.append(status)))
        assert statuses == [expected], statuses
        return body
    def deliver(payload, event=None, invalid=False):
        body = json.dumps(payload).encode()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        env = {'PATH_INFO': '/webhooks/github' if event else '/webhooks/linear',
               'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
        if event:
            env.update(HTTP_X_GITHUB_EVENT=event, HTTP_X_HUB_SIGNATURE_256='bad' if invalid else 'sha256=' + signature)
        else:
            env['HTTP_LINEAR_SIGNATURE'] = 'bad' if invalid else signature
        invoke(app, env, '400 Bad Request' if invalid else '200 OK')
    def scrape(authorization='Bearer ' + token):
        return invoke(metrics, {'PATH_INFO': '/metrics', 'REQUEST_METHOD': 'GET',
                               'HTTP_AUTHORIZATION': authorization},
                      '200 OK' if authorization == 'Bearer ' + token else '401 Unauthorized').decode()
    def stamp(seconds):
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')
    with patch('telemetry.service.EventStore', side_effect=lambda db: EventStore(db, clock=lambda: clock[0] * 1_000_000_000)), \
            patch('telemetry.metrics_http.render_metrics', side_effect=lambda db, repos: render_metrics(db, repos, now_ns=clock[0] * 1_000_000_000)):
        deliver({}, 'pull_request', invalid=True)
        deliver({}, invalid=True)
        scrape('Bearer invalid')
        with closing(connect()) as db:
            assert db.execute('SELECT count(*) FROM engineering_events').fetchone()[0] == 0
        for minute in range(61):
            clock[0] = start + minute * 60
            for index, (repo, first, inputs, outputs, review_rate, lag) in enumerate((
                    ('fixture/alpha', 6, 10, 2, 2, 5), ('fixture/beta', 9, 4, 1, 1, 9))):
                pr, issue = index + 1, 'KEB-' + str(131 + index)
                observed = clock[0] - lag
                base = {'repository': {'full_name': repo}, 'installation': {'id': 42}}
                for run in range(10 if minute else 1):
                    for attempt in range(1, 5 if minute else 3):
                        payload = dict(base, workflow_run={
                            'id': (index + 1) * 10_000_000 + minute * 100 + run,
                            'run_attempt': attempt, 'status': 'completed',
                            'conclusion': 'success' if not minute or attempt > 1 or run < first else 'failure',
                            'head_sha': 'a' * 40, 'updated_at': stamp(observed),
                            'pull_requests': [{'number': pr}]})
                        deliver(payload, 'workflow_run')
                        if run == 0 and attempt == 1:
                            deliver(payload, 'workflow_run')
                with closing(connect()) as db:
                    codex_turn(EventStore(db, clock=lambda: clock[0] * 1_000_000_000),
                               {'type': 'turn.completed', 'usage': {
                                   'input_tokens': inputs if minute else 0,
                                   'output_tokens': outputs if minute else 0}, 'prompt': marker},
                               f'turn-{index}-{minute}', repo, issue, f'run-{index}-{minute}',
                               'a' * 40, observed * 1_000_000_000)
                for review in range(review_rate + 1 if minute else 1):
                    identity = (index + 1) * 10_000_000 + minute * 100 + review
                    confirmed = not minute or review < review_rate
                    state = 'approved' if not minute or index == 0 and review == 0 else 'commented'
                    if confirmed and state == 'commented':
                        evidence[identity] = [{'id': identity + 500_000_000, 'pull_request_review_id': identity,
                                               'path': 'src/example.py', 'position': 4, 'body': marker}]
                    payload = dict(base, action='submitted', pull_request={
                        'number': pr, 'head': {'sha': 'a' * 40}}, review={
                        'id': identity, 'state': state, 'commit_id': 'a' * 40,
                        'submitted_at': stamp(observed), 'body': marker})
                    deliver(payload, 'pull_request_review')
                    deliver(payload, 'pull_request_review')
                deliver({'organizationId': 'fixture-org', 'webhookTimestamp': clock[0] * 1000,
                         'type': 'Issue', 'action': 'update', 'data': {
                             'id': 'issue-' + str(index), 'stateId': 'done',
                             'updatedAt': stamp(observed), 'description': marker}})
            snapshot = scrape()
            assert marker not in snapshot
            lines.extend(line + f' {clock[0]}.0' for line in snapshot.splitlines() if line and not line.startswith('#'))
        verified = 0
        def check(name, repo, value, **labels):
            nonlocal verified
            encoded = ','.join(key + '=' + json.dumps(item) for key, item in dict(repository=repo, **labels).items())
            assert f'{name}{{{encoded}}} {value}\n' in snapshot, (name, repo, labels, value)
            verified += 1
        for repo, first, inputs, outputs, reviews, lag in (
                ('fixture/alpha', 6, 10, 2, 2, 5), ('fixture/beta', 9, 4, 1, 1, 9)):
            check('agentic_ci_attempts_total', repo, 1 + 60 * first, attempt='1', outcome='success')
            check('agentic_ci_attempts_total', repo, 60 * (10 - first), attempt='1', outcome='failure')
            check('agentic_ci_attempts_total', repo, 1801, attempt='retry', outcome='success')
            check('agentic_tokens_total', repo, 60 * inputs, type='input')
            check('agentic_tokens_total', repo, 60 * outputs, type='output')
            check('agentic_review_cycles_total', repo, 1 + 60 * reviews)
            check('agentic_review_unclassified_total', repo, 60)
            for source in ('agent', 'ci', 'github', 'linear'):
                check('agentic_source_lag_seconds', repo, lag, source=source)
                check('agentic_source_timing_valid', repo, 1, source=source)
                check('agentic_source_receipt_age_seconds', repo, 0, source=source)
        with closing(connect()) as db:
            count = db.execute('SELECT count(*) FROM engineering_events').fetchone()[0]
            assert count == 5350, count
            assert marker not in '\n'.join(db.iterdump())
    assert clock[0] == end
    return lines, {'snapshots': 61, 'events': count, 'verified_final_samples': verified,
                   'negative_auth_checks': 3, 'repositories': list(repositories),
                   'producer_backed_panels': [2, 3, 5, 6, 15],
                   'contract_only_panels': [1, 4, 7, 8, 9, 10, 11, 12, 13, 14]}
