"""Project durable source records into bounded-cardinality cumulative metrics."""
from collections import Counter
import json
import re


def render_metrics(db, repositories, max_events=100000):
    repositories = frozenset(repositories)
    if (not repositories or len(repositories) > 100
            or any(not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repo) for repo in repositories)
            or type(max_events) is not int or not 1 <= max_events <= 1000000):
        raise ValueError('Bounded explicit repository scope required')
    placeholders = ','.join('?' for _ in repositories)
    rows = db.execute('SELECT payload FROM engineering_events WHERE '
                      "json_extract(payload, '$.repository') IN (" + placeholders + ') '
                      'ORDER BY source,event_id LIMIT ?', (*sorted(repositories), max_events + 1)).fetchall()
    if len(rows) > max_events:
        raise ValueError('Metric projection limit exceeded; reconciliation required')
    source_counts, ci_counts, tokens = Counter(), Counter(), Counter()
    review_counts, review_unknown = Counter(), Counter()
    attempts, reviews = {}, {}
    for (raw,) in rows:
        event = json.loads(raw)
        repo, source, attrs = event['repository'], event['source'], event['attributes']
        source_counts[repo, source] += 1
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
    return '\n'.join(lines) + '\n'
