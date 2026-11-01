# Engineering event ingestion (draft)

EventStore persists only normalized allowlisted metadata for agent, CI, GitHub and Linear sources. It deduplicates provider event IDs, rejects conflicting duplicates, retains out-of-order events and exposes a bounded durable outbox. Unknown cost/usage stays explicitly unknown, not zero. Stable trace/span identifiers are derived from verified repository/issue mapping and provider event identity.

This is not yet OpenTelemetry instrumentation or a Grafana exporter. The five current tests use normalized synthetic events, not four live authenticated source deliveries. Implement source adapters with signature/identity checks, verified issue mapping and source timestamps before calling this store. No raw webhook, prompt, review body, source code, email or credential may be stored. Do not pass a raw provider payload as attributes. Repository/issue correlation is approved resource metadata, not permission to collect personal attributes.

Use SQLite on a persistent encrypted volume with restricted permissions and approved retention. The outbox is at-least-once: a crash after external export and before acknowledgement can re-export an event. Deterministic IDs help correlation but do not guarantee backend deduplication. Do not derive current issue/PR state merely from arrival order; a future projection must handle source revisions/reconciliation explicitly. Database/file size, pruning, concurrent exporter claims and operational recovery remain to implement.

Next: instrument using the official OpenTelemetry Python SDK, map each authenticated source, correlate their spans, and test OTLP delivery/retry against a controlled receiver. Grafana stack/credentials/scope and production endpoints remain blocked on owner KEB-129 setup. No data has been exported and no Grafana resources created. KEB-130 acceptance requires actual source samples and end-to-end export, not these unit tests.

Sources reviewed: https://opentelemetry.io/docs/languages/python/instrumentation/ and https://linear.app/developers/webhooks .
