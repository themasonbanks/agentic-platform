# Stockpot audit — 31 October 2026

Source: local stockpot checkout, read-only inspection for KEB-118.

Existing reusable concepts: AGENTS.md, PR acceptance/validation/rollback template, definition of done, ADR template, least-privilege hosted CI with concurrency cancellation.
Stockpot-specific choices: Bun 1.3.14, Biome, Turbo, Expo/Hono/Supabase; preserve these in Stockpot. No pnpm migration is needed.
Existing CI covers PRs and main pushes; KEB-120 also requires feature-branch pushes, failure/repair evidence and required-check enforcement. Existing actions use version tags; pinning and scanner coverage remain KEB-121 work.
Database CI uses disposable local Supabase with policy tests and generated-type drift detection. Keep it application-specific; avoid adding a database to every template.
No changes were made to Stockpot or its worktrees by this task.

Linear still lists KEB-120/122 as Todo; existing files alone do not prove all acceptance criteria. Reconcile statuses only after hosted and human-review evidence is verified.

Both private repositories were created with owner approval. Remaining KEB-118 evidence: independent review, hosted workflow consumption/failure verification and measured fresh-repo adoption under 30 minutes.
