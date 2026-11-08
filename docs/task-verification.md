# Task verification

The controller registers a validated task with a unique run ID before reserving attempt one. The task snapshot and expiry survive restarts. Reserve an attempt before dispatching any implementation or repair work. A repeated reservation returns false. The controller can inspect the current attempt and resume it from its own checkpoint without dispatching duplicate work.

The controller collects check results and criterion evidence independently from the implementation worker. Each result names the approved task snapshot, exact candidate commit and its approved source. Evidence also names the criterion it proves. Only the controller translates a trusted source result into a structured observation. Command output and worker messages carry no authority over checks, evidence or permissions.

Recording an attempt saves the verification manifest in one transaction. All required checks and evidence must pass for a verified outcome. Missing or blocked results escalate. A known failure permits repair while attempts and time remain. A repair requires a fresh verification of the resulting commit. The controller owns budget enforcement, permission checks, execution leases and publish approval.

The manifest contains the run, issue, repository, task digest, candidate commit, attempt, check results, individual criterion results and decision. Keep it in controller storage for use when producing the draft pull request summary.
