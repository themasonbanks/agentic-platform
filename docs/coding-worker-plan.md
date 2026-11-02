# Coding worker integration plan

The coding executor remains unimplemented and disabled. Official Codex non-interactive guidance supports codex exec with JSONL events and structured final output. On GitHub Actions it recommends the Codex GitHub Action, which brokers model requests through a Responses API proxy, rather than placing an API key in the environment of repository-controlled code.

Use a dedicated approved automation identity and a scoped secret reference. Never copy the desktop account's auth.json into public-repository CI. The official guidance explicitly excludes that account-auth workflow for public/open-source repositories. No credentials have been inspected, copied or exported for this proposal; only CLI help was read. API-key automation is separately billed and needs the owner's explicit monthly ceiling and model choice before a paid test.

## Proposed execution boundary

The owner approved £5 per calendar month for automated coding-model calls on 2 November 2026, separately from cloud development costs. The current ledger counts micro-USD, so enabling it requires a trusted conservative conversion that includes applicable fees and rounds the available allowance down. Do not configure £5 as $5, infer a current exchange rate or let repository input choose conversion/pricing. Missing or expired conversion evidence must block paid execution. The approved amount does not enable the worker or replace the identity, model, token/runtime and dry-run gates below.

1. Trusted webhook intake authenticates delivery, refetches current review metadata, commits durable work and produces only references.
2. Trusted coordinator verifies current configuration-bound owner enablement, exact PR head, concurrency/attempt limits and a durable worst-case cost reservation. Unknown price or missing identity/budget blocks execution.
3. A disposable hosted worker fetches the exact approved head. No GitHub App private key, webhook secret, admin credentials or personal account tokens enter the coding environment. A reviewed model proxy supplies scoped inference access outside untrusted repository processes.
4. The coding executor has a reviewed pinned version and restricted sandbox, network destinations, file roots, token/output limits and enforced wall timeout. The BusyBox no-network probe establishes a baseline only; it does not verify the required inference-only network gateway or writable coding workspace.
5. A trusted supervisor takes the actual Git diff, derives its tree/manifest, rejects sensitive paths, and reruns the complete approved check set in a separate isolated environment. It records sanitized metadata and explicit unknown costs without persisting prompts/review bodies.
6. A separate publisher authenticates as the approved App and atomically updates only the same PR branch against the reviewed head. No approval/merge endpoint or bypass permission is granted. Head changes, disagreements, sensitive patches, cost overruns and exhausted attempts require human intervention.
7. Before autonomy, perform and obtain owner approval of five real dry-run artifacts under that exact configuration, then separately enable the gate. The current unit-test records are not those approvals.

## Remaining evidence

Before configuring any paid invocation, review the exact GitHub Action implementation/version and permissions, choose the model and known price/budget, and agree the App/endpoint/agent identity. Then verify end-to-end synthetic finding -> isolated fix -> trusted checks -> same-branch push -> hosted CI -> sanitized explanation. Test malicious text, races, retries, process termination, unknown prices and credential exposure. The current libraries and fixed container probes are supporting components, not proof of this flow.

Owner account setup still blocks live AWS deployment, Sentry and Grafana verification. Existing board assignments KEB-123/126/129 remain the owner action list. KEB-128 remains In Progress; this plan does not replace its full acceptance criteria.

Sources checked 1 November 2026:
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://learn.chatgpt.com/docs/auth
