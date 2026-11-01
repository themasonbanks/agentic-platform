# Engineering event ingestion (draft)

EventStore persists only normalized allowlisted metadata for agent, CI, GitHub and Linear sources. It deduplicates provider event IDs, rejects conflicting duplicates, retains out-of-order events and exposes a bounded durable outbox. Unknown cost/usage stays explicitly unknown, not zero. Stable trace/span identifiers are derived from verified repository/issue mapping and provider event identity.

The SDK export adapter now emits normalized events as OpenTelemetry spans; Grafana export remains unconfigured. The five current tests use normalized synthetic events, not four live authenticated source deliveries. Implement source adapters with signature/identity checks, verified issue mapping and source timestamps before calling this store. No raw webhook, prompt, review body, source code, email or credential may be stored. Do not pass a raw provider payload as attributes. Repository/issue correlation is approved resource metadata, not permission to collect personal attributes.

Use SQLite on a persistent encrypted volume with restricted permissions and approved retention. The outbox is at-least-once: a crash after external export and before acknowledgement can re-export an event. Deterministic IDs help correlation but do not guarantee backend deduplication. Do not derive current issue/PR state merely from arrival order; a future projection must handle source revisions/reconciliation explicitly. Database/file size, pruning, concurrent exporter claims and operational recovery remain to implement.

Next: instrument using the official OpenTelemetry Python SDK, map each authenticated source, correlate their spans, and test OTLP delivery/retry against a controlled receiver. Grafana stack/credentials/scope and production endpoints remain blocked on owner KEB-129 setup. No data has been exported and no Grafana resources created. KEB-130 acceptance requires actual source samples and end-to-end export, not these unit tests.

Sources reviewed: https://opentelemetry.io/docs/languages/python/instrumentation/ and https://linear.app/developers/webhooks .

## OpenTelemetry SDK verification

Pinned opentelemetry-sdk/api 1.45.2 with semantic-conventions 0.66b2 and typing-extensions 4.16.0; Python 3.10+ is required. Local tests use an isolated temporary Python 3.15 environment; CI creates its own venv. The adapter supplies only the fixed service resource and allowlisted event attributes, correlates spans through a derived repository/issue trace context and acknowledges the outbox only after synchronous exporter success. Unknown cost/usage flags remain explicit.

Seven tests pass, including actual SDK export of four synthetic normalized source events into an in-memory receiver, shared trace correlation and export-failure retention. The parent context is synthetic correlation metadata, not evidence of a real recorded orchestration root span. Actual authenticated source ingestion, OTLP network delivery, Grafana acceptance, durable exporter claims/retention and cost attribution remain to implement. No prompts, review bodies or model credentials are exported.

## Source adapters

Added metadata normalization for GitHub workflow_run, PR lifecycle and terminal Codex JSONL turn events. Workflow attempt counts are preserved; skipped/neutral/unknown results are not mislabeled success. PR titles/bodies/users and Codex prompt/command/item text are not copied. Token usage is included only when reported; no price is inferred. The trusted caller supplies verified issue mapping and stable event IDs. Signature/replay/repository authorization must be enforced before these functions; they do not authenticate raw webhooks by themselves.

Ten local tests pass. A real read-only GitHub API result for platform run41336441106 (KEB-125) was normalized, persisted to a temporary in-memory store and delivered through the SDK into an in-memory receiver; no raw API payload was logged or exported. This verifies one live source snapshot, not four authenticated live webhook deliveries or Grafana ingestion. Linear normalization/signature handling and actual orchestration root spans remain required. Source timestamp parsing expects timezone-aware ISO timestamps; outbox delivery remains at-least-once.
