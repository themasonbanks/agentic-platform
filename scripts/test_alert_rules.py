"""Generate native promtool cases and validate explicit threshold configuration."""
import copy
import json
from pathlib import Path
import unittest
from build_alert_rules import build, METRICS


FIXTURE = {'repositories': ['fixture/alpha'], 'window_seconds': 300, 'hold_seconds': 120,
           'pending_seconds': 30, 'ci_failure_fraction': .5, 'ci_minimum_attempts': 10,
           'token_limit': 1000, 'recorded_cost_usd_limit': 1,
           'deployment_failure_limit': 2, 'source_lag_seconds': 60}
ALERTS = ('AgenticRemediationStuck', 'AgenticCIFailureSpike', 'AgenticTokenSpike',
          'AgenticRecordedCostSpike', 'AgenticDeploymentFailures', 'AgenticTelemetryLag',
          'AgenticTelemetryMissing')


def fixture_files(directory):
    rules = build(FIXTURE)
    (directory / 'alert-rules.json').write_text(json.dumps(rules))
    definitions = rules['groups'][0]['rules']
    def series(violated=False, overrides=None, repo='fixture/alpha'):
        values = [('agentic_remediation_pending_age_seconds', '', '100+0x15' if violated else '0+0x15'),
                  ('agentic_ci_attempts_total', ',outcome="success"', '0+2x15' if violated else '0+10x15'),
                  ('agentic_ci_attempts_total', ',outcome="failure"', '0+8x15' if violated else '0+0x15'),
                  ('agentic_tokens_total', ',type="input"', '0+300x15' if violated else '0+1x15'),
                  ('agentic_tokens_total', ',type="output"', '0+100x15' if violated else '0+1x15'),
                  ('agentic_cost_usd_total', '', '0+0.5x15' if violated else '0+0x15'),
                  ('agentic_deployments_total', ',outcome="failure"', '0+1x15' if violated else '0+0x15'),
                  ('agentic_source_lag_seconds', '', '90+0x15' if violated else '5+0x15')]
        return [{'series': name + '{repository=' + json.dumps(repo) + labels + '}',
                 'values': (overrides or {}).get(name + labels, value)} for name, labels, value in values]
    def case(name, inputs, firing=(), at='7m'):
        checks = []
        for alert in ALERTS:
            expected = [{'exp_labels': rule['labels'], 'exp_annotations': rule['annotations']}
                        for rule in definitions if rule['alert'] == alert and alert in firing]
            checks.append({'eval_time': at, 'alertname': alert, 'exp_alerts': expected})
        return {'name': name, 'interval': '1m', 'input_series': inputs, 'alert_rule_test': checks}
    tests = [case('healthy explicit zeroes', series()),
             case('all configured business and lag limits exceeded', series(True), ALERTS[:-1]),
             case('conditions pending before hold duration', series(True), at='1m'),
             case('missing data is telemetry unknown, not a business incident', [], (ALERTS[-1],)),
             case('small CI denominator suppresses spike', series(overrides={
                 'agentic_ci_attempts_total,outcome="success"': '0+0x15',
                 'agentic_ci_attempts_total,outcome="failure"': '0+1x15'})),
             case('other repository failures do not leak into selected scope', series() + series(True, repo='fixture/beta')),
             case('counter resets retain token spikes', series(overrides={
                 'agentic_tokens_total,type="input"': '0+400x3 0+400x11'}), ('AgenticTokenSpike',)),
             case('remediation recovers after age drops', series(overrides={
                 'agentic_remediation_pending_age_seconds': '100+0x7 0+0x7'}), at='10m')]
    (directory / 'alert-tests.json').write_text(json.dumps({
        'rule_files': ['/fixture/alert-rules.json'], 'evaluation_interval': '1m', 'tests': tests}))
    return len(tests)


class ConfigurationTests(unittest.TestCase):
    def test_rejects_missing_unknown_and_invalid_thresholds(self):
        variants = []
        for key in FIXTURE:
            value = copy.deepcopy(FIXTURE)
            del value[key]
            variants.append(value)
        for key, invalid in [('hold_seconds', True), ('window_seconds', 119), ('hold_seconds', 0),
                             ('ci_failure_fraction', 1.1), ('token_limit', 1.5),
                             ('recorded_cost_usd_limit', float('nan')),
                             ('recorded_cost_usd_limit', float('inf')),
                             ('repositories', ['fixture/alpha', 'fixture/alpha']),
                             ('repositories', ['fixture/alpha"} or vector(1)']),
                             ('repositories', []), ('extra', 'unused')]:
            variants.append(dict(FIXTURE, **{key: invalid}))
        for configuration in variants:
            with self.subTest(configuration=configuration), self.assertRaises(ValueError):
                build(configuration)


if __name__ == '__main__':
    unittest.main()
