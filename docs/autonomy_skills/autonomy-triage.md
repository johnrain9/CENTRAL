# autonomy-triage

Purpose:

- Diagnose failed runs and stalled review items from CLI-visible evidence.
- Choose an explicit next action: approve, reject, reset, retry, or leave blocked.

Bootstrap contract:

- Activate `/home/cobra/photo_auto_tagging/.venv` before using `autonomy ...`
- If `autonomy` is missing, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Inspect worker artifacts and logs.
- Pull failure context from reports and task state.
- Keep manual decisions auditable with explicit commands and notes.
- Use the canonical CENTRAL task file to verify intended scope and acceptance for planner-owned work.

Command dependencies:

- `autonomy report failures --json --profile default`
- `autonomy report review-aging --json --profile default`
- `autonomy task show <id> --json --profile default`
- `autonomy worker inspect <id> --json --profile default`
- `autonomy worker tail <id> --profile default`
- `autonomy task reset <id> --profile default`
- `autonomy worker retry <id> --profile default`
- `autonomy task approve <id> --reviewer "..." --profile default`
- `autonomy task reject <id> --reviewer "..." --notes "..." --profile default`

Decision rules:

- Approve when acceptance is met and evidence is concrete.
- Reject when scope, correctness, or evidence is insufficient and replanning is required.
- Reset for transient runtime or environment failures before a clean rerun.
- Retry only when the previous run provides enough evidence to justify another attempt.
- Leave blocked when dependencies or external inputs still prevent progress.

Task reference rule:

- If the run maps to a planner-owned CENTRAL task, review `CENTRAL/tasks/<TASK_ID>.md` before approving, rejecting, or resetting so the decision stays anchored to the authored contract.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
