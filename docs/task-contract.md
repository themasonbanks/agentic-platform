# Task contract v1

The task declaration records one Linear issue, its approved repository and branch, the worktree identifier, the intended changes and evidence for each acceptance criterion. The checked-in JSON schema describes the shape. Call `task_contract.preflight.validate_task(task, policy)` before dispatch to enforce the controller's policy. A validation error blocks launch.

The controller supplies the policy independently from the task declaration. It binds the issue to a repository and approves a specific branch and worktree, paths, tools, required checks, evidence sources, human gates and upper limits. Check identifiers refer to commands in the controller's approved catalog. For example, a controller can bind `diff-check` to the argument list `["git", "diff", "--check"]`. The task carries the identifier and cannot change the command.

The worktree field identifies an approved checkout. The controller must resolve the identifier and verify its actual repository, branch, commit and writable paths before execution. The validator checks declared scope and leaves runtime isolation, state transitions and gate decisions to the controller. A declared terminal outcome is a vocabulary item. Only the controller assigns the outcome after verification.

To adopt the contract, retain the project instructions and PR template. Translate the issue objective and acceptance criteria into a task declaration, choose the matching entries in the approved check and evidence catalogs, and validate before launching. Continue using the PR template to report actual evidence and unresolved criteria. Contracts record required evidence and do not represent check results.

See [the valid Stockpot example](../examples/stockpot-task.json) and [the rejected deployment example](../examples/unsafe-task.json). Both are fixtures for preflight validation.
