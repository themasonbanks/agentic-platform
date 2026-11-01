# Engineering alert rules

`scripts/build_alert_rules.py` renders Prometheus-compatible data source rules from explicit operator thresholds. It does not contact Grafana, install rules, configure notification routing or enable a paid service. These are not Grafana-managed provisioning API payloads. Choose the Cloud metrics ruler or a reviewed Grafana-managed conversion when the collection pipeline is ready; the OTLP gateway is not a rule-management endpoint.

Copy `alert-thresholds.template.json` outside the repository, fill every field with approved values, then run:

```sh
python3 scripts/build_alert_rules.py /private/tmp/agentic-alert-thresholds.json > /private/tmp/agentic-alert-rules.json
```

The empty template deliberately fails validation. The builder has no production threshold defaults. It accepts one to twenty explicit repositories; no cross-repository regular expression or unbounded discovery. Review generated rules before provisioning into a dedicated namespace. Existing rules must not be overwritten. No credential is needed to generate or test rules.

| Field | Meaning |
| --- | --- |
| window_seconds | Rolling counter window, integer 120–3600 seconds |
| hold_seconds | Continuous condition duration before firing, integer 60–3600 seconds |
| pending_seconds | Age limit for the oldest actionable remediation |
| ci_failure_fraction | Failed / success-or-failure completed CI attempts; fraction greater than zero and at most one, including retries |
| ci_minimum_attempts | Minimum completed-attempt denominator before a failure spike can fire |
| token_limit | Reported input plus output tokens per window; cached-input subtypes are not added again |
| recorded_cost_usd_limit | Reconciled actual USD charges per window, still partial if some runs have unknown charges |
| deployment_failure_limit | Terminal deployment failures per window; count at or above this limit fires |
| source_lag_seconds | Maximum reported source lag |

Business, delivery-lag and timing-health rules use fixed metric names and exact repository selectors. Missing input does not become zero or an incident in another category. Separate `AgenticTelemetryMissing` rules cover each required metric. Producers must emit explicit zeroes when they can establish complete zero activity; an absent cost producer means unknown coverage. Prometheus lookback/staleness and the configured hold can delay missing-data alerts. A stale but continuously scraped source-lag gauge requires producer reconciliation; this package cannot establish source completeness by itself. None of these alerts is a spending cap or a replacement for the remediation executor's enforced limits.

The required dashboard CI job validates threshold rejection and runs native `promtool test rules` inside the pinned, isolated Prometheus image. Synthetic cases cover firing, healthy zeroes, pending duration, minimum CI samples, missing data, repository isolation, counter resets and remediation recovery. Fixture thresholds are deliberately synthetic and have no authorization to run against owner data.

Before live acceptance: verify metric coverage and clocks, approve thresholds and retention, select Mason's contact destination, provision in the UK Cloud stack through a restricted credential, and deliver/recover a controlled test notification. Configure evaluator/datasource execution failures as a separate telemetry-health notification; successful rule unit tests do not test an unavailable ruler or notification delivery. Record native Cloud rule state and delivery evidence. No Cloud rule or contact point has been created.

References: [Prometheus rule unit tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/) and [Grafana alert provisioning API formats](https://grafana.com/docs/grafana-cloud/observe-and-act/alert-and-measure-reliability/alerting/set-up/provision-alerting-resources/http-api-provisioning/).

AgenticTelemetryTimingUnknown fires when any observed source timing_valid is below one, independently of lag or business thresholds. Legacy untimed records and detected source/receiver clock inconsistencies are unverified timing, not zero latency. Required-metric missing-data coverage includes the timing-valid metric itself. Native fixtures also test an isolated timing anomaly without a business incident. Source receipt age is a separate signal; an approved expected-source/idle policy is still needed before silence alerts can be configured.
