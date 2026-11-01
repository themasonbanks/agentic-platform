# Agentic platform

Shared automation for independently managed application repositories. Published public scaffold.

## Available interface

.github/workflows/project-checks.yml is a reusable Ubuntu quality job. It checks out the caller's code and invokes python3 scripts/run_ci.py, with read-only contents permission, no persisted checkout credentials and a 20-minute timeout, and seven-day report artifacts even on failure. New consumers must include scripts/run_ci.py from the template. Older consumers remain pinned to older workflow commits.
Application scripts own tool setup, frozen dependency installation and real checks. Never call this workflow with secrets or a self-hosted personal Mac runner.

Mason approved creation of both private repositories. Shared workflow access is enabled for repositories owned by themasonbanks; Actions may expose workflow logs/content to collaborators of consuming repositories. Consumers must pin a reviewed full commit SHA, enable cross-repository Actions access where needed and verify a failing and repaired test PR.

## Layout and ownership

- .github/workflows: reusable quality workflow; CI expansion tracked in KEB-120.
- terraform/modules: reserved infrastructure modules; KEB-124/125.
- docs: boundaries, adoption and audit findings.

No cloud resources, remediation service or telemetry are provisioned by this scaffold.

## Reusable security workflow

.github/workflows/security-checks.yml adds full-history redacted Gitleaks, high/critical Trivy dependency/IaC checks and supported-language CodeQL analysis with a severity gate. Callers grant contents read and security-events write; only the CodeQL job uses the write scope. The workflow declares no inherited secrets. Pin a reviewed commit and include its result in required CI. Optional verify-fixtures runs the template detection tests; leave false in consumers without that test script.
