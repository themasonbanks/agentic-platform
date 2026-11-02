"""Project durable source records into bounded-cardinality cumulative metrics."""
from collections import Counter
import json
import re
import time
from telemetry.leadtime import lead_time_metrics


def repository_scope(repositories):
    if (not isinstance(repositories, (list, tuple, set, frozenset)) or not 1 <= len(repositories) <= 100
            or any(not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repo)
                   for repo in repositories)):
        raise ValueError('Bounded explicit repository scope required')
    return frozenset(repositories)


def render_metrics(db, repositories, max_events=100000, now_ns=None):
    repositories = repository_scope(repositories)
    if type(max_events) is not int or not 1 <= max_events <= 1000000:
        raise ValueError('Bounded event history required')
    now_ns = time.time_ns() if now_ns is None else now_ns
    if type(now_ns) is not int or not 0 <= now_ns <= 9_223_372_036_854_775_807:
        raise ValueError('Bounded trusted scrape timestamp required')
    placeholders = ','.join('?' for _ in repositories)
    rows = db.execute('SELECT e.payload,r.sequence,r.received_ns FROM engineering_events e '
                      'LEFT JOIN engineering_receipts r ON e.source=r.source AND e.event_id=r.event_id WHERE '
                      "json_extract(e.payload, '$.repository') IN (" + placeholders + ') '
                      'ORDER BY e.source,e.event_id LIMIT ?', (*sorted(repositories), max_events + 1)).fetchall()
    if len(rows) > max_events:
        raise ValueError('Metric projection limit exceeded; reconciliation required')
    source_counts, ci_counts, tokens = Counter(), Counter(), Counter()
    review_counts, review_unknown = Counter(), Counter()
    attempts, reviews, receipts, highwater = {}, {}, {}, {}
    lead_time_records = []
    for raw, sequence, received_ns in rows:
        event = json.loads(raw)
        lead_time_records.append((event, sequence))
        repo, source, attrs = event['repository'], event['source'], event['attributes']
        source_counts[repo, source] += 1
        if sequence is not None:
            if (type(sequence) is not int or sequence <= 0 or type(received_ns) is not int
                    or not 0 <= received_ns <= 9_223_372_036_854_775_807):
                raise ValueError('Invalid durable receipt metadata')
            if sequence > receipts.get((repo, source), (0,))[0]:
                receipts[repo, source] = (sequence, received_ns, event['observed_ns'])
            highwater[repo, source] = max(highwater.get((repo, source), 0), received_ns)
        if source == 'ci' and event['kind'] in {'success', 'failure'}:
            identity = (repo, attrs['run_id'], attrs['retry_count'])
            if identity in attempts and attempts[identity] != event['kind']:
                raise ValueError('Conflicting terminal workflow outcome')
            if identity not in attempts:
                attempts[identity] = event['kind']
                attempt = '1' if attrs['retry_count'] == 0 else 'retry'
                ci_counts[repo, attempt, 'success'] += int(event['kind'] == 'success')
                ci_counts[repo, attempt, 'failure'] += int(event['kind'] == 'failure')
        if source == 'agent':
            for label, field in (('input', 'input_tokens'), ('output', 'output_tokens')):
                if attrs.get(field) is not None:
                    tokens[repo, label] += attrs[field]
        if source == 'github' and event['kind'] == 'reviewed':
            identity = (repo, attrs['pr'], attrs['review_id'])
            details = (attrs['sha'], event['issue'], event['observed_ns'],
                       attrs['review_state'], attrs['review_classification'])
            if identity in reviews and reviews[identity] != details:
                raise ValueError('Conflicting submitted review metadata')
            if identity not in reviews:
                reviews[identity] = details
                if attrs['review_classification'] in {'decision', 'inline'}:
                    review_counts[repo,] += 1
                    review_unknown[repo,] += 0
                else:
                    review_unknown[repo,] += 1
    lines = []
    for name, help_text, labels, values in (
            ('agentic_metric_source_events_total', 'Durable source records observed, not completeness.',
             ('repository', 'source'), source_counts),
            ('agentic_ci_attempts_total', 'Distinct terminal workflow attempts; non-success/failure excluded.',
             ('repository', 'attempt', 'outcome'), ci_counts),
            ('agentic_tokens_total', 'Reported per-turn tokens; absent usage is omitted.',
             ('repository', 'type'), tokens),
            ('agentic_review_cycles_total', 'Observed confirmed review submissions; partial coverage.',
             ('repository',), review_counts),
            ('agentic_review_unclassified_total', 'Observed review submissions without substantive-review evidence.',
             ('repository',), review_unknown)):
        lines.extend((f'# HELP {name} {help_text}', f'# TYPE {name} counter'))
        for key, value in sorted(values.items()):
            encoded = ','.join(label + '=' + json.dumps(part) for label, part in zip(labels, key))
            lines.append(f'{name}{{{encoded}}} {value}')
    timing, lag, age, skew = {}, {}, {}, {}
    for key in source_counts:
        receipt = receipts.get(key)
        timing[key] = 0
        if receipt is None:
            continue
        _, received_ns, observed_ns = receipt
        skew[key] = max(observed_ns - received_ns, 0) / 1_000_000_000
        if now_ns >= received_ns:
            age[key] = (now_ns - received_ns) / 1_000_000_000
        if observed_ns <= received_ns <= now_ns and received_ns == highwater[key]:
            timing[key] = 1
            lag[key] = (received_ns - observed_ns) / 1_000_000_000
    for name, help_text, values in (
            ('agentic_source_timing_valid', 'Latest durable receipt has consistent source/receiver/scrape clocks.', timing),
            ('agentic_source_lag_seconds', 'Latest durable receipt time minus source timestamp; invalid timing omitted.', lag),
            ('agentic_source_receipt_age_seconds', 'Scrape time minus latest durable receipt; replay does not refresh.', age),
            ('agentic_source_clock_skew_seconds', 'Source timestamp ahead of latest durable receipt, otherwise zero.', skew)):
        lines.extend((f'# HELP {name} {help_text}', f'# TYPE {name} gauge'))
        for (repo, source), value in sorted(values.items()):
            lines.append(f'{name}{{repository={json.dumps(repo)},source={json.dumps(source)}}} {value:.9g}')
    lines.extend(lead_time_metrics(lead_time_records, now_ns))
    return '\n'.join(lines) + '\n'
