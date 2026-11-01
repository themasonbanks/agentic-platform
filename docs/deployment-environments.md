# Stockpot deployment environment controls

Configuration read back from GitHub on1November2026 for themasonbanks/stockpot:

| Environment | Allowed branch policy | Required reviewer |
| --- | --- | --- |
| development | main branch only; no tag rule | themasonbanks |
| production | main branch only; no tag rule | themasonbanks |
| terraform-production-plan | main branch only; no tag rule | themasonbanks |
| terraform-development-plan | No branch restriction; review required for every job | themasonbanks |

All four environments have prevent_self_review=true and can_admins_bypass=false. Branch-policy API readback confirms exactly one branch rule, main, for each restricted environment. The development-plan environment permits reviewed feature-branch plans; this does not authorize untrusted/fork code to receive credentials. Workflows must also enforce the trusted repository/event/head before entering an environment.

These settings match the bootstrap's four exact OIDC environment names. Stockpot's verified immutable subject prefix remains repo:themasonbanks@79837282/stockpot@1319669903. An AWS role must require that exact prefix plus its intended environment and sts.amazonaws.com audience. Environment names alone do not prove branch approval or role isolation. No AWS roles/resources or environment credentials were created in this configuration step.

The current desktop GitHub session acts as themasonbanks. Because self-review is blocked, that identity cannot approve a deployment job it initiated. Configure the separately approved automation/App identity before expecting Mason to approve agent-initiated runs. Do not relax self-review or use administrator bypass to work around missing identity setup. The remediation publisher has no environment-approval or merge operation.

Configuration readback is not runtime deployment evidence. Before binding cloud credentials, use a reviewed no-credential probe to verify a feature branch/tag cannot enter production, a main job waits for Mason, the initiating identity cannot self-review and administrators cannot bypass the wait. Cancel probe jobs without approving a production deployment. Then verify live OIDC subject/role separation using approved temporary credentials. No probe or apply workflow has been run, no release was approved and no cloud resources were created.

GitHub's REST environment API accepted can_admins_bypass=false, and a separate GET verified the stored value; it must be set explicitly because the creation default was true. API reference: https://docs.github.com/en/rest/deployments/environments . Protection semantics: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments .
