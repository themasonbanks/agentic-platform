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

## Linear signed issue adapter

`receive_issue` verifies HMAC-SHA256 over the original bounded raw bytes using constant-time comparison, then requires the signed webhookTimestamp to be within 60 seconds of a trusted receiver clock. Organization, issue UUID-to-repository/identifier mapping and state UUID mapping are explicit trusted configuration. Only Issue create/update events are accepted; unknown scope/state fails closed. Actor, title, description and other payload content are discarded. A logical event key uses signed issue/update/state/action metadata so repeated delivery does not create more state events.

Three additional tests cover signed normalization/deduplication, wrong signatures, replay/future timestamps, organization/issue scope, unknown state and boolean timestamp rejection. Production HTTPS endpoint, secret provisioning, real signed Linear delivery and four-source live correlation remain unverified. Linear requires HTTP200 for successful delivery and retries non-200 failures; the HTTP adapter preserves that contract for accepted deliveries and durable duplicates. See https://linear.app/developers/webhooks .

## Live PR snapshot verification

Read-only GitHub REST data for merged platform PR #6 was normalized through the existing snake-case updated_at adapter and exported through the SDK to an in-memory receiver. The resulting span was engineering.github.merged with repository/KEB-120/PR6 metadata; no title, body, user or credentials were exported. The adapter already used the correct GitHub field, so no code change was required. This is a reconciliation snapshot test using the actual REST response, not proof of a signed webhook delivery or issue-to-merge lead-time accuracy. The CI and PR source read paths now both have live-snapshot evidence; Linear/Codex live delivery and Grafana acceptance remain missing.

## Authenticated GitHub telemetry intake

`telemetry.github.receive` validates HMAC over the bounded raw body before parsing or resolving issue mappings, then enforces repository-to-installation allowlisting. It accepts only workflow_run/PR events and requires a trusted resolver to establish the issue relationship. PR deduplication uses signed lifecycle metadata rather than an unsigned delivery header or a digest of private body text. No raw payload is persisted. Three additional tests verify signature-before-resolver ordering, installation scope, missing mappings and replay/privacy behavior; all 16 tests pass locally.

The resolver must use verified project/PR/commit relationships, not blindly trust issue-looking strings in titles or body text. GitHub has no signed global delivery timestamp here; durable logical deduplication handles exact lifecycle replays, while reconciliation and bounded storage/retention remain operational requirements. Live signed GitHub delivery and deployed HTTP routing are not established by these synthetic tests.

## Webhook HTTP adapter

telemetry.http.application accepts an explicit path-to-receiver mapping. Bind separate GitHub and Linear routes to the authenticated receivers above; callbacks must complete durable storage before returning and obtain a per-request database connection. It returns HTTP200 for valid deliveries including duplicates, HTTP400 for invalid signatures/payloads and HTTP503 for storage/I/O failures without exposing exception details. Unknown routes, methods, content types, missing/invalid lengths, oversized bodies and truncated reads are rejected before invoking receivers. Requests are limited to 1MiB.

A production WSGI server and TLS proxy must enforce request/read timeouts, connection limits, header limits and body limits before dispatch; this adapter does not provide those controls. No server is deployed. Three adapter tests exercise transport rejection, signed Linear delivery and replay into the real event store, and retryable database failure. The complete local suite passes 19 tests. Real GitHub HTTP delivery, source mapping configuration and live deployment remain outstanding.

telemetry.service.create_application wires /webhooks/github and /webhooks/linear to the corresponding authenticated receivers. Supply a connection factory opening the same durable SQLite database, separate webhook secrets, verified installation/organization/issue/state mappings, and the trusted GitHub issue resolver. Each request opens and closes its own connection, including on errors; no connection is shared across WSGI threads. The factory rejects absent secrets/mappings at startup and snapshots mapping dictionaries. A signed GitHub fixture traverses the configured HTTP route into a file-backed database; duplicate requests use separate closed connections and leave one event. The complete local suite now passes 20 tests. This remains local fixture evidence, not a deployed GitHub webhook or live acceptance.
