# Bounded review remediation

This is the durable intake and dry-run scheduler for KEB-128. It is not a deployed webhook service or an enabled coding worker. There is no GitHub write client, agent runner, approval API or merge API in this package. CodeRabbit review text is untrusted data, never a command. No source files, comment bodies, prompts, tokens or webhook secrets are stored in its database or exported job requests.

## Current interface

`Intake.receive(event, signature, raw_body)` checks an HMAC-SHA256 signature with constant-time comparison before parsing JSON or fetching GitHub data. Supply a secret of at least 32 bytes from secret storage, explicit repository-to-installation IDs and the API-verified CodeRabbit bot user ID. A deployment adapter must preserve the exact raw request bytes and reject requests over 1 MiB before buffering. It must translate malformed JSON/fields/signatures to generic HTTP errors without logging request contents. Unsupported events/actions are ignored.

The injected `ReviewSource.current_review` must fetch the PR, comment and its current thread through the authenticated GitHub API. It must verify the comment belongs to that PR, resolve author identity by numeric ID, and determine resolved/outdated status from thread metadata. Missing or ambiguous metadata must fail closed. The webhook's embedded body/head/author are not trusted as current evidence. API errors must not acknowledge successful processing: retry delivery after bounded backoff and respect GitHub rate limits.

Only open same-repository feature PRs with an unresolved, current-head CodeRabbit comment can queue. Main/master are denied; the future adapter must also deny every protected/default branch discovered from repository policy. Sensitive paths and suspicious review text go to needs-human. This classifier is conservative routing, not a proof against prompt injection. Raw review text never reaches the scheduler's output. A coding worker will need independent verification of the reported bug from source before deciding whether to act.

SQLite stores a unique repository/PR/head/comment key. Edited comments for an existing key do not create more work; the scheduler refetches current review/head/thread metadata before claiming. Changes invalidate the candidate. Duplicate delivery and process restart do not reset the queue or attempt count. Keep this database on a persistent encrypted volume, restrict file permissions to the service user, back it up and define a retention policy before hosting. It is not suitable on ephemeral function storage. One transaction and a unique partial index enforce one running attempt per PR across database connections.

`claim_dry_run` returns a reference-only request with a maximum 300-second deadline, dry-run mode and zero spend limit. At most three total attempts are reserved per PR across comments and head changes; this is stricter than three retries. A failed/expired attempt requires human review. Expired running leases remain locked until a human verifies that the old worker has stopped; they are never automatically reused. A worker supervisor must enforce process termination at the deadline: the database deadline alone cannot terminate a process. `finish_dry_run` records completion and rejects duplicate completion. These scheduler records are not owner approvals.

## Worker and publishing design

Before adding a live adapter, Mason must approve the GitHub App identity, repository selection, endpoint and spend ceiling. Proposed permissions: metadata read, pull requests read, contents write only where branch patch publication is approved, and narrowly scoped issue comments for explanations. Do not grant administration, workflows write, bypass rights or pull-request review/merge authority. Confirm the current GitHub endpoint permission requirements when implementing the adapter; do not silently increase permissions. Required PR checks and branch protection must stay enforced.

The intake host authenticates a webhook, commits durable work, then responds quickly. A separate worker claims a job and fetches the exact head into an ephemeral isolated workspace. It has no personal Mac credentials, webhook secret, GitHub App private key or cloud credentials. Its network access must be limited to the approved model gateway, dependencies and source-fetch mechanism; it cannot run untrusted PR code on a personal self-hosted runner. It receives finding references, verifies the bug and produces an artifact containing a patch, changed-file manifest, explanation and check results. Disagreements and security-sensitive changes go to Mason.

A trusted supervisor enforces the wall-time ceiling and atomically reserves the approved worst-case cost before starting an agent. Unknown prices or missing budgets must block a paid run; actual cost must not be guessed. The owner-approved monthly ceiling is separate from the £5–£10 AWS development target. Retries and a changed head cannot reset the spend ledger. A crash retains reservations until reconciled. The initial implementation intentionally has no paid execution path.

A separate minimal publisher validates the patch against the original head and permitted paths, rejects workflow/auth/infrastructure changes, independently reruns required checks in the isolated environment and rechecks the live head. It may update only the same approved PR branch using compare-and-swap; a moved head causes needs-human, never a force push. An installation-token push should trigger hosted checks. Publishing an explanation must use a reviewed sanitized template and must not quote untrusted review instructions. The service must not submit approvals, resolve disagreements by itself or merge. Installation permissions and protected-branch rules provide additional enforcement beyond the absence of those API methods.

## Enablement and remaining acceptance

Autonomous execution remains disabled until all of these are evidenced:

1. Owner approves App permissions, installation, webhook endpoint, agent identity and monthly ceiling; live signature handling and persistent encrypted storage are verified.
2. Validate the metadata adapter with the approved App installation, and implement/test the isolated coding worker, enforced timeout/cost ledger, patch validator, check rerun and same-branch publisher. No stub is accepted as a working coding adapter.
3. Run a synthetic real CodeRabbit finding through one verified safe fix, same-branch commit, hosted CI rerun and explanation. Run malicious text, stale head, concurrency, delivery retry, exhausted retry and unknown-cost cases; observe needs-human where required.
4. Mason approves each of the first five dry runs. Persist separate owner identities/approval records outside repository-controlled input and audit the artifacts. The existing dry-run completion field does not satisfy this gate.
5. Verify that the App cannot approve or merge and that the owner can disable intake/workers immediately. Only then may an explicit owner setting enable autonomous runs within the approved limits.

The current 19 local tests verify intake/scheduling invariants using a fake metadata source. They do not prove a safe code fix, live CI rerun, effective GitHub App permissions, process isolation, hard runtime termination or owner approval. Keep KEB-128 In Progress until those requirements pass.

References checked 1 November 2026: [GitHub signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) and [review-comment events](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request_review_comment).

## Read-only GitHub adapter

`GitHubReviewSource` implements current PR/comment/thread lookup using `GitHubJSON` and an injected temporary-token provider. The transport uses fixed api.github.com endpoints, refuses redirects, caps responses at 2 MiB and times out each request after ten seconds. It binds comment ID/PR URL, supports only root findings, checks protected/default branches and drafts, and scans at most ten pages of 100 thread roots. Missing threads, replies, incomplete metadata, query errors and invalid pagination fail closed. This bounded lookup intentionally escalates unusually large PRs instead of guessing. The publisher must still recheck the live head immediately before any future branch update.

Six additional adapter tests use fake API responses. A read-only live query verified the thread fields against GitHub; a live approved App installation and full transport integration are still unverified. Do not infer deployed authentication from that schema probe.

## Durable budget ledger

`Budget` reserves integer micro-USD worst-case costs atomically against a separately owner-approved UTC monthly ceiling. Missing limits/prices block reservation. A run identity cannot be reserved twice; retries use new identities against the same shared ceiling. Unknown outcomes retain their reservations across crashes/restarts. Known actual cost is reconciled once; conflicting settlements fail, while overruns are recorded and block further spending. There is no automatic release of abandoned work.

This ledger is not wired to a paid executor and does not itself stop API billing. A trusted supervisor must supply the current UTC period, calculate a verified worst-case price, enforce model/token/runtime limits, stop jobs at month boundaries, reconcile actual costs and escalate overruns. Repository/webhook input must never choose the ceiling, period, price or settlement. Cross-provider currency conversion and billing lag still need explicit handling before enabling paid execution. Six budget tests exercise shared limits, restart retention, unknown prices, settlement, overruns and retries; 25 local tests now pass overall.

## Publication validation primitives

`validate_manifest` accepts at most twenty ordinary non-executable file additions/modifications beneath explicit approved roots. It rejects traversal, ambiguous/duplicate paths, hidden files, symlinks, executable modes, deletions, dependency manifests and sensitive paths. `authorize_publication` requires an unchanged exact PR head and successful required checks tied to the proposed patch tree, including the complete trusted required-check set. Five tests cover these boundaries; 30 local tests pass overall.

These functions are not a publisher or a sandbox. Their manifest and check evidence must be derived by a trusted supervisor from actual Git objects and independently executed checks, never accepted from agent-produced JSON. The future publisher must verify patch size/content, derive the complete manifest including renames/modes, validate the resulting commit/tree, recheck branch protection and update the same branch with atomic expected-head semantics. No GitHub write operation is implemented here. A valid manifest does not prove safe source-code behaviour. Integration and adversarial patch tests remain required before enabling publication.
