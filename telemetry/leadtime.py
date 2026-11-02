"""Join observed issue creation and first merge without rewriting histogram history."""
from collections import Counter
import json

BOUNDS = (60, 300, 600, 1800, 3600, 14400, 86400, 604800, 2592000)
REASONS = ('missing_issue_creation', 'missing_merge_time', 'invalid_timing', 'legacy_receipt')


def lead_time_metrics(records, now_ns):
    """Project receipt-ordered joins; require reconciliation for retroactive first merges."""
    created, merges, identities, completed, legacy = {}, {}, {}, {}, set()
    relevant = []
    for event, sequence in records:
        if event['source'] == 'linear' and 'issue_created_ns' in event['attributes']:
            relevant.append((sequence, event))
        elif event['source'] == 'github' and event['kind'] == 'merged':
            relevant.append((sequence, event))
    for sequence, event in sorted(relevant, key=lambda item: (item[0] is not None, item[0] or 0)):
        repo, issue, attrs = event['repository'], event['issue'], event['attributes']
        key = (repo, issue)
        if sequence is None:
            legacy.add(key)
        if event['source'] == 'linear':
            value = attrs['issue_created_ns']
            if key in created and created[key] != value:
                raise ValueError('Conflicting issue creation time; reconciliation required')
            created[key] = value
        else:
            pr = attrs.get('pr')
            identity = (repo, pr) if pr else (repo, event['event_id'])
            value = attrs.get('merged_ns')
            if identity in identities:
                previous_issue, previous_time = identities[identity]
                if previous_issue != issue or previous_time is not None and value is not None and previous_time != value:
                    raise ValueError('Conflicting associated merge; reconciliation required')
                value = previous_time if value is None else value
            identities[identity] = (issue, value)
            merges.setdefault(key, {})[identity] = value
        times = merges.get(key, {})
        if key in legacy or key not in created or not times or None in times.values():
            if key in completed:
                raise ValueError('Completed lead-time coverage changed; reconciliation required')
            continue
        first = min(times.values())
        if not created[key] <= first <= now_ns:
            if key in completed:
                raise ValueError('Completed lead-time timing changed; reconciliation required')
            continue
        if key in completed and completed[key] != first:
            raise ValueError('Historical first merge changed; rebuild metric baseline after reconciliation')
        completed[key] = first
    eligible = {repo for (repo, issue), value in created.items() if value <= now_ns}
    buckets, sums, counts, incomplete = Counter(), Counter(), Counter(), Counter()
    for key, times in merges.items():
        repo, issue = key
        if key in legacy:
            reason = 'legacy_receipt'
        elif key not in created:
            reason = 'missing_issue_creation'
        elif None in times.values():
            reason = 'missing_merge_time'
        elif not created[key] <= min(times.values()) <= now_ns:
            reason = 'invalid_timing'
        else:
            reason = None
        if reason:
            incomplete[repo, reason] += 1
            continue
        seconds = (completed[key] - created[key]) / 1_000_000_000
        counts[repo] += 1
        sums[repo] += seconds
        for bound in BOUNDS:
            buckets[repo, bound] += int(seconds <= bound)
    name = 'agentic_issue_to_merge_seconds'
    lines = [f'# HELP {name} Observed mapped issue creation to first associated merge; partial history.',
             f'# TYPE {name} histogram']
    for repo in sorted(eligible):
        label = 'repository=' + json.dumps(repo)
        for bound in BOUNDS:
            lines.append(f'{name}_bucket{{{label},le="{bound}"}} {buckets[repo, bound]}')
        lines.extend((f'{name}_bucket{{{label},le="+Inf"}} {counts[repo]}',
                      f'{name}_sum{{{label}}} {sums[repo]:.17g}', f'{name}_count{{{label}}} {counts[repo]}'))
    name = 'agentic_issue_to_merge_incomplete'
    lines.extend((f'# HELP {name} Observed merged issues excluded from lead time, not delivery completeness.',
                  f'# TYPE {name} gauge'))
    for repo in sorted({repo for repo, issue in merges} | eligible):
        for reason in REASONS:
            lines.append(f'{name}{{repository={json.dumps(repo)},reason="{reason}"}} {incomplete[repo, reason]}')
    return lines
