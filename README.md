# Agentic platform

Shared automation for independently managed application repositories. Local KEB-118 scaffold; not published.

## Available interface

.github/workflows/project-checks.yml is a reusable Ubuntu quality job. It checks out the caller's code and invokes sh scripts/ci.sh, with read-only contents permission, no persisted checkout credentials and a 20-minute timeout.
Application scripts own tool setup, frozen dependency installation and real checks. Never call this workflow with secrets or a self-hosted personal Mac runner.

Publish only after owner approval of repository creation, visibility and access. Consumers must pin a reviewed full commit SHA, enable cross-repository Actions access where needed and verify a failing and repaired test PR.

## Layout and ownership

- .github/workflows: reusable quality workflow; CI expansion tracked in KEB-120.
- terraform/modules: reserved infrastructure modules; KEB-124/125.
- docs: boundaries, adoption and audit findings.

No cloud resources, remediation service or telemetry are provisioned by this scaffold.
