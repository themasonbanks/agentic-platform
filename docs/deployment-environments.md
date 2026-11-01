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

Configuration readback alone is not runtime deployment evidence. The no-credential probe was reviewed and merged in Stockpot PR #10, commit f4f2d3a614e9fb45751c94dc3914c5dd0e35dbb7; its post-merge main CI41344673105 passed. Controlled dispatches verified the negative gate behavior below. No release was approved, no approval/bypass API was called and no cloud resources were created. Live OIDC subject/role separation and a successful owner-reviewed run from the separately approved automation identity remain unverified.

| Probe | Workflow run | Observed gate behavior |
| --- | --- | --- |
| production, feature branch | [41344695555](https://github.com/themasonbanks/stockpot/actions/runs/41344695555) | Rejected by branch policy |
| production plan, feature branch | [41344758342](https://github.com/themasonbanks/stockpot/actions/runs/41344758342) | Rejected by branch policy |
| production, temporary tag | [41345003969](https://github.com/themasonbanks/stockpot/actions/runs/41345003969) | Rejected by tag policy |
| production plan, temporary tag | [41345011456](https://github.com/themasonbanks/stockpot/actions/runs/41345011456) | Rejected by tag policy |
| production, main | [41344753327](https://github.com/themasonbanks/stockpot/actions/runs/41344753327) | Waited for Mason; cancelled without approval |
| production plan, main | [41344763162](https://github.com/themasonbanks/stockpot/actions/runs/41344763162) | Waited for Mason; cancelled without approval |
| development, main | [41344768123](https://github.com/themasonbanks/stockpot/actions/runs/41344768123) | Waited for Mason; cancelled without approval |
| development plan, feature branch | [41344772679](https://github.com/themasonbanks/stockpot/actions/runs/41344772679) | Waited for Mason; cancelled without approval |

The pending-deployments API named themasonbanks as reviewer and returned current_user_can_approve=false for the initiating themasonbanks session in all four waiting cases. Rejection annotations explicitly named branch/tag environment protection rules. Every gate job had zero steps and no assigned runner; every run was confirmed terminal. The temporary lightweight tag agentic-gate-probe-20261101-7622540 pointed only to the reviewed probe commit and was removed after checking its exact object ID; matching-ref readback returned zero. Administrator bypass remains verified by stored can_admins_bypass=false, not by attempting a bypass request. Main probe head was f4f2d3a614e9fb45751c94dc3914c5dd0e35dbb7; feature/tag head was7622540d4dafe5e115301a068d6ce85bec91aaf5.

GitHub's REST environment API accepted can_admins_bypass=false, and a separate GET verified the stored value; it must be set explicitly because the creation default was true. API reference: https://docs.github.com/en/rest/deployments/environments . Protection semantics: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments .
