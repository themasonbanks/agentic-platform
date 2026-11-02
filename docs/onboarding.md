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

Prepare real scripts/setup.sh and scripts/check.sh in the application and commit them first. Preflight requires tracked files and rejects comments-only bodies and common no-op commands. This is a placeholder check, not proof of test coverage or shell correctness; review the adapters and verify actual failing/repaired hosted checks. Obtain the reviewed template commit 2d26fa1817ec20dd67690bd7853917a4483e8c37 in a local template checkout. From the platform checkout run:

```sh
python3 scripts/bootstrap_project.py /path/to/app --template-checkout /path/to/template
python3 scripts/bootstrap_project.py /path/to/app --template-checkout /path/to/template --write
```

Default mode only lists proposed files. Write mode refuses dirty trees, existing destinations and existing workflow configurations; those require a manual integration PR rather than overlapping CI. It adds instructions, review policy, runner, pinned workflow and adoption evidence. It does not run app code, create a branch, commit, push, create a repository, change visibility, grant permissions, provision resources or enable remediation. Review the entire diff, complete project architecture, create one issue branch and one PR, then validate actual checks. An interrupted write may leave uncommitted files; inspect them and remove only bootstrap-owned files before retrying. Never reset unrelated work.

Preflight uses a restricted Git environment, disables hooks and filesystem monitoring, ignores replacement objects and reads template files as raw pinned blobs. Repositories with configured filters (including Git LFS), partial clones or submodules require manual integration. These safeguards reduce executable Git behavior; they are not an operating-system sandbox for hostile repositories. The generated workflows reference reviewed platform commit 3f910d6dcfa3cde52b62912c4155bf5dd62ef40b.

New repositories may be generated directly from the GitHub template with owner-authorized repository creation. Complete their setup/check adapter and project instructions before first merge. Do not automatically create a second pilot repository until Stockpot's full end-to-end trial passes.

## GitHub and review acceptance

Quality jobs use contents read only, hosted runners and no inherited secrets. CodeQL's job needs security-events write and actions read; private repositories must verify entitlement before adoption. Check cross-repository workflow access, full commit pins and selected-repository App installations. Require the stable CI check with no bypass, PRs and up-to-date branches; choose human review requirements appropriate to available reviewers. Verify a deliberately failing PR blocks merge, then a repaired push passes. Confirm CodeRabbit's effective policy, actionable findings and phone readability; a neutral/rate-limited status is not review evidence. Keep automatic code changes disabled until the bounded service has passed all acceptance and owner gates.

## Per-app infrastructure and service setup

Each app gets explicit development/production state paths, repository/environment-bound OIDC trust and scoped roles. Configure GitHub environment approvals and production branch restrictions; do not give app code the bootstrap identity. Reuse an existing database when appropriate; do not provision databases, NAT gateways or load balancers by habit. Review a real plan, region, recurring costs and temporary AWS authentication before apply. London's current platform development target is approximately £5/month with an upper alert around £10; alerts do not cap spending and are not an approval for a new paid service.

For the reviewed bootstrap, create development, production, terraform-development-plan and terraform-production-plan environments with the approved human reviewer, self-review prevention and administrator bypass disabled. Restrict development apply, production apply and production plan to an explicit main branch rule with no tag rule. Feature-branch development plans still require trusted-code review before state credentials. Inspect persisted settings and branch rules after creation; environment creation defaults may permit administrator bypass. Verify the current repository's OIDC customization API instead of copying Stockpot's immutable repository IDs. Scope every AWS trust policy to that exact repository/environment and sts.amazonaws.com audience.

A shared desktop identity acting as the owner cannot approve its own runs when self-review is blocked. Use the separately approved automation identity for agent-initiated deployment jobs; never relax the gate to bypass missing identity setup. Before attaching credentials, review a no-credential/manual gate probe: no checkout, secret references, token permissions or deployment commands; explicit target allowlist before entering an environment. Verify feature/tag rejection and main pending approval, then cancel probes without approval/bypass calls. Configuration readback alone is not runtime gate acceptance. The production Terraform acknowledgement boolean is also not a substitute for this actual human authorization boundary.

For static hosting, the current draft shares one environment composition between independent development/production roots, with London private S3/KMS and a dedicated us-east-1 global CloudFront WAF exception. AWS's FREE CloudFront plan is separate from a new-account Free Tier account plan; the latter is ineligible until upgraded. FREE subscriptions are limited to 3 per AWS account, shared across every app/environment/preview, not 3 per repository. Verify remaining slots and eligible AWS-managed cache/WAF features before allocation. Do not automatically create separate distributions per PR or both live environments for every app. Additional apps may require a reviewed shared-hosting design or another cost proposal, not an automatic paid upgrade/fallback. Separate KMS keys, storage/version history and other uncovered features remain chargeable; do not claim the whole platform is free. Subscription creation/failure/cancellation can leave pay-as-you-go resources, so verify ACTIVE status before publishing and keep cancellation/destruction manual. References: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html and https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plans.html .

Create distinct Sentry projects/environments and Grafana project labels with limited ingestion credentials and approved retention. Use secret-manager/GitHub-environment references, never pasted keys, prompts or personal data. Configure release IDs, repo, issue, PR, SHA and run correlation; display unknown costs explicitly. Verify a non-sensitive Sentry test exception and correlated Grafana events/dashboards before claiming monitoring is live. Public client DSNs do not justify exposing admin/source-map-upload tokens to application bundles.

## Backup, recovery and rollout

Verify encrypted/versioned remote state, lock contention, version restoration and dev/prod isolation. Owner-only bootstrap state and backups stay outside application CI access. Document data backup/restore procedures, deployment rollback, preview teardown on PR closure, cost attribution and manual destruction. Rehearse recovery using disposable data; never reset a production database for a test.

Owner-only actions include account creation/MFA/billing, App/OAuth authorizations, AWS trust/bootstrap plan approval, secrets setup, production gates and the first five remediation dry-run approvals. The agent prepares code and evidence first. No broad personal credentials or monorepo migration is required.

## Completion evidence

One reviewed application PR must include preflight results, actual quality/security/review checks, required-check failure/repair evidence, phone verification, approved deployment/monitoring setup and recovery evidence. Keep unsupported integrations explicitly pending. KEB-133 remains In Progress until Stockpot's end-to-end pilot succeeds and a second independently managed app adopts and verifies this process. No second-product adoption is claimed by local fixtures.
