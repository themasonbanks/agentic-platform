"""Build repository-scoped Prometheus rules from explicit operator thresholds."""
import argparse
import json
import math
from pathlib import Path
import re


KEYS = {'repositories', 'window_seconds', 'hold_seconds', 'pending_seconds',
        'ci_failure_fraction', 'ci_minimum_attempts', 'token_limit',
        'recorded_cost_usd_limit', 'deployment_failure_limit', 'source_lag_seconds'}
METRICS = ('agentic_remediation_pending_age_seconds', 'agentic_ci_attempts_total',
           'agentic_tokens_total', 'agentic_cost_usd_total',
           'agentic_deployments_total', 'agentic_source_lag_seconds', 'agentic_source_timing_valid')


def build(configuration):
    if not isinstance(configuration, dict) or set(configuration) != KEYS:
        raise ValueError('All threshold fields must be explicit; unknown fields are rejected')
    repositories = configuration['repositories']
    if (not isinstance(repositories, list) or not 1 <= len(repositories) <= 20
            or any(not isinstance(repo, str) or len(repo) > 201
                   or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
                   for repo in repositories) or len(set(repositories)) != len(repositories)):
        raise ValueError('One to twenty unique owner/repository names required')
    for key in KEYS - {'repositories'}:
        value = configuration[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1_000_000_000:
            raise ValueError('Positive finite bounded thresholds required')
    for key in ('window_seconds', 'hold_seconds', 'ci_minimum_attempts', 'token_limit',
                'deployment_failure_limit'):
        if type(configuration[key]) is not int:
            raise ValueError('Durations and count thresholds must be integers')
    if not 120 <= configuration['window_seconds'] <= 3600 or not 60 <= configuration['hold_seconds'] <= 3600:
        raise ValueError('Window must be 120–3600s and hold must be 60–3600s')
    if configuration['ci_failure_fraction'] > 1:
        raise ValueError('CI failure fraction cannot exceed one')
    window = str(configuration['window_seconds']) + 's'
    rules = []
    for repo in repositories:
        def metric(name, labels=''):
            return name + '{repository=' + json.dumps(repo) + labels + '}'
        def increase(name, labels=''):
            return 'sum(increase(' + metric(name, labels) + '[' + window + ']))'
        def add(name, expression, category, summary, extra=None):
            rules.append({'alert': name, 'expr': expression,
                          'for': str(configuration['hold_seconds']) + 's',
                          'labels': dict(repository=repo, severity='warning', category=category, **(extra or {})),
                          'annotations': {'summary': summary}})
        add('AgenticRemediationStuck', 'max(' + metric(METRICS[0]) + ') > ' + str(configuration['pending_seconds']),
            'remediation', 'Oldest actionable remediation exceeds the configured age limit')
        failures = increase(METRICS[1], ',outcome="failure"')
        attempts = increase(METRICS[1], ',outcome=~"success|failure"')
        add('AgenticCIFailureSpike', failures + ' / ' + attempts + ' > ' + str(configuration['ci_failure_fraction'])
            + ' and (' + attempts + ' >= ' + str(configuration['ci_minimum_attempts']) + ')',
            'ci', 'CI failure fraction and minimum completed-attempt denominator exceeded')
        add('AgenticTokenSpike', increase(METRICS[2], ',type=~"input|output"') + ' > ' + str(configuration['token_limit']),
            'usage', 'Reported input and output tokens exceed the configured window limit')
        add('AgenticRecordedCostSpike', increase(METRICS[3]) + ' > ' + str(configuration['recorded_cost_usd_limit']),
            'cost', 'Recorded actual USD charges exceed the window limit; coverage may be partial')
        add('AgenticDeploymentFailures', increase(METRICS[4], ',outcome="failure"') + ' >= '
            + str(configuration['deployment_failure_limit']), 'deployment',
            'Terminal deployment failures exceed the configured window count')
        add('AgenticTelemetryLag', 'max(' + metric(METRICS[5]) + ') > ' + str(configuration['source_lag_seconds']),
            'telemetry', 'Reported source lag exceeds the configured limit')
        add('AgenticTelemetryTimingUnknown', 'min(' + metric(METRICS[6]) + ') < 1',
            'telemetry', 'Source or receiver timing is invalid or unverified')
        for name in METRICS:
            add('AgenticTelemetryMissing', 'absent(' + metric(name) + ')', 'telemetry',
                'Required metric is missing; business health and cost coverage are unknown', {'metric': name})
    return {'groups': [{'name': 'agentic-engineering', 'interval': '60s', 'rules': rules}]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('configuration', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(build(json.loads(arguments.configuration.read_text())), indent=2))
