# Product onboarding

Shared automation belongs in agentic-platform. New products start from agentic-project-template; existing products keep their own repositories. Application-specific instructions/checks and service configuration stay with the application. Template copies do not update themselves; shared workflow pins advance through reviewed PRs.

## Compatibility

| Product | Required quality checks | Additional acceptance | Hosting |
| --- | --- | --- | --- |
| Static web | Frozen install, format/lint/types where applicable, tests, build | Browser/phone preview, accessibility | Simplest appropriate static hosting; AWS only after cost/IAM review |
| Mobile | App runtime checks, domain tests, build/export where supported | Native simulator/device, secure storage/deep links, iPhone review | Mobile distribution is separate from API/web hosting |
| Server/API | Frozen install, lint/types, unit/integration tests | Auth boundaries, disposable database/migrations, rollback | Reviewed per-app AWS resources; avoid paid database/network defaults |

Use each project's existing package manager and lockfile. Never add pnpm to a Bun project by default. Missing checks must fail preflight, not become passing placeholders. Optional scripts/check-integration.sh runs after ordinary checks; scripts/check-e2e.sh runs on PR events. Source-only projects must document justified omissions.

## Bootstrap a clean existing repository

Prepare real scripts/setup.sh and scripts/check.sh in the application and commit them first. Obtain the reviewed template commit 2d26fa1817ec20dd67690bd7853917a4483e8c37 in a local template checkout. From the platform checkout run:

```sh
python3 scripts/bootstrap_project.py /path/to/app --template-checkout /path/to/template
python3 scripts/bootstrap_project.py /path/to/app --template-checkout /path/to/template --write
```

Default mode only lists proposed files. Write mode refuses dirty trees, existing destinations and existing workflow configurations; those require a manual integration PR rather than overlapping CI. It adds instructions, review policy, runner, pinned workflow and adoption evidence. It does not run app code, create a branch, commit, push, create a repository, change visibility, grant permissions, provision resources or enable remediation. Review the entire diff, complete project architecture, create one issue branch and one PR, then validate actual checks. An interrupted write may leave uncommitted files; inspect them and remove only bootstrap-owned files before retrying. Never reset unrelated work.

New repositories may be generated directly from the GitHub template with owner-authorized repository creation. Complete their setup/check adapter and project instructions before first merge. Do not automatically create a second pilot repository until Stockpot's full end-to-end trial passes.

## GitHub and review acceptance

Quality jobs use contents read only, hosted runners and no inherited secrets. CodeQL's job needs security-events write and actions read; private repositories must verify entitlement before adoption. Check cross-repository workflow access, full commit pins and selected-repository App installations. Require the stable CI check with no bypass, PRs and up-to-date branches; choose human review requirements appropriate to available reviewers. Verify a deliberately failing PR blocks merge, then a repaired push passes. Confirm CodeRabbit's effective policy, actionable findings and phone readability; a neutral/rate-limited status is not review evidence. Keep automatic code changes disabled until the bounded service has passed all acceptance and owner gates.

## Per-app infrastructure and service setup

Each app gets explicit development/production state paths, repository/environment-bound OIDC trust and scoped roles. Configure GitHub environment approvals and production branch restrictions; do not give app code the bootstrap identity. Reuse an existing database when appropriate; do not provision databases, NAT gateways or load balancers by habit. Review a real plan, region, recurring costs and temporary AWS authentication before apply. London's current platform development target is approximately £5/month with an upper alert around £10; alerts do not cap spending and are not an approval for a new paid service.

Create distinct Sentry projects/environments and Grafana project labels with limited ingestion credentials and approved retention. Use secret-manager/GitHub-environment references, never pasted keys, prompts or personal data. Configure release IDs, repo, issue, PR, SHA and run correlation; display unknown costs explicitly. Verify a non-sensitive Sentry test exception and correlated Grafana events/dashboards before claiming monitoring is live. Public client DSNs do not justify exposing admin/source-map-upload tokens to application bundles.

## Backup, recovery and rollout

Verify encrypted/versioned remote state, lock contention, version restoration and dev/prod isolation. Owner-only bootstrap state and backups stay outside application CI access. Document data backup/restore procedures, deployment rollback, preview teardown on PR closure, cost attribution and manual destruction. Rehearse recovery using disposable data; never reset a production database for a test.

Owner-only actions include account creation/MFA/billing, App/OAuth authorizations, AWS trust/bootstrap plan approval, secrets setup, production gates and the first five remediation dry-run approvals. The agent prepares code and evidence first. No broad personal credentials or monorepo migration is required.

## Completion evidence

One reviewed application PR must include preflight results, actual quality/security/review checks, required-check failure/repair evidence, phone verification, approved deployment/monitoring setup and recovery evidence. Keep unsupported integrations explicitly pending. KEB-133 remains In Progress until Stockpot's end-to-end pilot succeeds and a second independently managed app adopts and verifies this process. No second-product adoption is claimed by local fixtures.
