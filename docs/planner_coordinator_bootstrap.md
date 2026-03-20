# Planner/Coordinator Bootstrap Guide

This document is the practical starting point for a human or AI acting as a planner/coordinator for the CENTRAL task system.

It answers:

- what repo to use
- what commands exist
- how to start and inspect the dispatcher
- how to inspect tasks and audits
- where the current ad hoc test and review tools live

## Current Status

The intended planner/operator surface is usable by both humans and AI.

However, this specific checkout currently has a temporary blocker:

- [dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)
- [central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)

Both currently contain merge-conflict markers, which means the direct CLI entrypoints are not executable until that conflict is resolved.

So:

- the workflow documented here is the intended and tested operator workflow
- this checkout needs those conflicts resolved before the commands can be run successfully

## Canonical Repo

Planner/coordinator work starts in:

- [CENTRAL](/Users/paul/projects/CENTRAL)

CENTRAL is the control plane:

- canonical task DB
- planner tooling
- dispatcher launcher/wrapper
- runtime status
- generated planner/operator views

## Canonical Rule

The CENTRAL SQLite DB is the source of truth.

Use:

- [central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)
- [create_planner_task.py](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py)
- [dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)
- [central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py)

Do not treat:

- [tasks.md](/Users/paul/projects/CENTRAL/tasks.md)
- [tasks](/Users/paul/projects/CENTRAL/tasks)
- [generated](/Users/paul/projects/CENTRAL/generated)

as canonical truth.

## Environment

Run from:

```bash
cd /Users/paul/projects/CENTRAL
```

Useful paths:

- live DB: [state/central_tasks.db](/Users/paul/projects/CENTRAL/state/central_tasks.db)
- runtime state: [state/central_runtime](/Users/paul/projects/CENTRAL/state/central_runtime)
- dispatcher log: [state/central_runtime/dispatcher.log](/Users/paul/projects/CENTRAL/state/central_runtime/dispatcher.log)

Shell wrapper:

- if your shell has the helper loaded, use `dispatcher ...`
- otherwise call [dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py) directly

## Dispatcher Basics

Direct script path:

```bash
python3 /Users/paul/projects/CENTRAL/scripts/dispatcher_control.py <command>
```

Wrapper form:

```bash
dispatcher <command>
```

Common commands:

```bash
dispatcher start --max-workers 1
dispatcher status
dispatcher workers
dispatcher logs
dispatcher follow
dispatcher stop
```

Important operator actions:

```bash
dispatcher kill-task <TASK_ID> --reason "operator stopped bad run" --json
```

What these do:

- `start`: launches the dispatcher daemon
- `status`: shows high-level runtime/queue status
- `workers`: shows active and recent worker health
- `logs` / `follow`: inspect dispatcher log output
- `kill-task`: explicit operator stop intent that fails a task and prevents immediate retry
- `stop`: stops the dispatcher

## Planner DB Basics

Direct script path:

```bash
python3 /Users/paul/projects/CENTRAL/scripts/central_task_db.py <command>
```

Initialize if needed:

```bash
python3 scripts/central_task_db.py init
```

Useful inspection commands:

```bash
python3 scripts/central_task_db.py status --json
python3 scripts/central_task_db.py task-list --json
python3 scripts/central_task_db.py task-show --task-id CENTRAL-OPS-28 --json
python3 scripts/central_task_db.py view-eligible --json
python3 scripts/central_task_db.py view-summary --json
python3 scripts/central_task_db.py view-review --json
python3 scripts/central_task_db.py view-assignments --json
```

What they are for:

- `status`: DB existence and migration state
- `task-list`: all canonical tasks
- `task-show`: one task card with events/artifacts
- `view-eligible`: dispatchable tasks now
- `view-summary`: portfolio summary
- `view-review`: failed/review/runtime-exception work
- `view-assignments`: leases and assignment state

## Creating Tasks

For normal planner use, prefer:

- [create_planner_task.py](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py)

Example:

```bash
python3 scripts/create_planner_task.py \
  --task-id CENTRAL-OPS-999 \
  --title "Example task" \
  --summary "Short summary" \
  --objective "Primary intended outcome." \
  --context "Why this task exists." \
  --scope "What is and is not in scope." \
  --deliverables "Concrete outputs expected." \
  --acceptance "Conditions for correctness." \
  --testing "Validation commands or expectations." \
  --dispatch "Worker execution guidance." \
  --closeout "What the worker should report." \
  --reconciliation "Planner-side post-execution handling." \
  --priority 10 \
  --json
```

Notes:

- implementation tasks auto-create paired audit tasks unless audit is disabled
- prefer `--audit-mode full` for non-trivial or higher-risk implementation work
- use `--audit-mode light` for bounded lower-risk implementation work such as focused tests, observability, or narrow slices where independent verification is still useful but a broad audit would be overkill
- use `--audit-mode none` or `--no-audit` only for trivial or explicitly exempt work
- use `--planner-status awaiting_audit` for truthful backfill of already-landed work

Audit mode guidance:

- `full`
  Use when the change could still be wrong even if local tests pass.
  Good fits:
  - lifecycle/state-machine changes
  - persistence/recovery
  - user-visible workflow changes
  - cross-repo contracts
  - security/boundary logic
  - anything where whole-system fit matters

- `light`
  Use when the task is real but tightly bounded.
  Good fits:
  - focused test additions
  - observability/reporting changes
  - narrow implementation slices with clear local acceptance
  - work where one or two decisive validation paths should be enough

- `none`
  Use sparingly.
  Good fits:
  - trivial tasks
  - explicit exemptions
  - tasks where independent audit cost clearly outweighs risk

## Audits

Audit-coupled behavior is now a core part of the system.

Core rules:

- most non-trivial implementation tasks should have a paired audit task
- the audit task is created immediately when the implementation task is created
- the audit task depends on the parent implementation task
- the audit task is usually not eligible at first
- it becomes eligible when the parent reaches `awaiting_audit`
- when the audit succeeds, both the audit task and the parent implementation task close to `done`
- when the audit fails, the parent does not close; follow-up or rework is required

Why this exists:

- implementation success is not the same as “the correct thing was built”
- the audit task exists to verify requirement fidelity, reality-based behavior, and whole-system fit

Typical lifecycle:

- implementation task starts in `todo`
- paired audit task is also created immediately in `todo`
- worker completes it
- implementation moves to `awaiting_audit`
- paired audit task becomes eligible
- successful audit closes both the audit and parent implementation task

Audit intent:

- did the implementation satisfy the stated objective and acceptance criteria
- did it work in the real environment, not just in isolated tests
- did it fit the broader system, instead of being a narrow local optimization

Auditor fix policy:

- auditors may apply bounded fixups when the problem is small, obvious, and local
- auditors should not silently absorb broad rework, architecture changes, or scope reinterpretation
- if the fix is substantial or ambiguous, the audit should fail or trigger follow-up work instead of turning into a second implementation pass

Dispatch implications:

- audit tasks exist from the start, but they are dependency-blocked until the parent is ready
- once an audit becomes eligible, it should usually be treated as high-priority follow-through work

If an audit fails:

- the parent implementation task should not silently become `done`
- the parent implementation task should move to `failed`
- the system should preserve the audit evidence
- bounded fixes may be possible depending on policy
- otherwise a follow-up implementation task should be created

## Handling Failed Tasks

Not all failed items mean the same thing. Treat them in buckets:

### Runtime failure on an implementation task

Examples:

- worker exited without result file
- result parse failed
- max retries exceeded

First inspect:

```bash
python3 scripts/central_task_db.py view-review --json
python3 scripts/central_task_db.py task-show --task-id <TASK_ID> --json
```

If the failure was operational/transient and the task should be retried:

```bash
python3 scripts/central_task_db.py runtime-requeue-task \
  --task-id <TASK_ID> \
  --reason "planner approved retry after investigation" \
  --reset-retry-count \
  --json
```

If the active worker must be stopped immediately:

```bash
python3 scripts/dispatcher_control.py kill-task <TASK_ID> \
  --reason "operator stopped bad run" \
  --json
```

If the task is obsolete or should stay failed, leave it failed and do not requeue it.

### Failed audit

When an audit fails:

- the audit task is `failed`
- the parent implementation task should also be `failed`
- planner should inspect the audit evidence and decide whether to create follow-up work

Inspect with:

```bash
python3 scripts/central_task_db.py view-audits --section failed --json
python3 scripts/central_task_db.py task-show --task-id <AUDIT_TASK_ID> --json
python3 scripts/central_task_db.py task-show --task-id <PARENT_TASK_ID> --json
```

Normal planner action after a failed audit:

- do not mark the original task back to `todo`
- preserve the failed audit as history
- create a new implementation task if follow-up work is needed
- link/group it with the same `initiative` when appropriate

Use a new task when:

- requirements were missed
- scope needs correction
- follow-up implementation work is clearly bounded

### Manual reconciliation

Use manual reconcile only when you are intentionally recording planner truth, not as a routine substitute for runtime automation.

Command:

```bash
python3 scripts/central_task_db.py task-reconcile \
  --task-id <TASK_ID> \
  --expected-version <VERSION> \
  --outcome failed|awaiting_audit|done \
  --summary "..." \
  --notes "..." \
  --json
```

Use this when:

- correcting state after a known operational anomaly
- backfilling planner truth
- closing a task manually after explicit planner review

Do not use it to erase real audit failures or hide runtime problems.

Useful audit inspection:

```bash
python3 scripts/central_task_db.py task-show --task-id CENTRAL-OPS-32 --json
python3 scripts/central_task_db.py view-eligible --json
python3 scripts/central_task_db.py task-list --json
```

What to look for:

- parent task `planner_status = awaiting_audit`
- audit child in `todo` and eligible
- accepted audits eventually close parent to `done`
- failed audits should leave a clear trail of evidence and move the parent to `failed`

## Checking Active Work

When the dispatcher is running, use:

```bash
dispatcher status
dispatcher workers
```

These surfaces should answer:

- is dispatcher running
- how many workers are active
- what task is running now
- what is parked and why
- whether worker logs are growing, flat, or stale

If a task looks suspicious:

```bash
dispatcher workers
dispatcher follow
python3 scripts/central_task_db.py task-show --task-id <TASK_ID> --json
```

## Runtime/Worker Inspection

Useful runtime command:

```bash
python3 scripts/central_runtime.py worker-status --json
```

This is the low-level view for:

- active worker lease state
- heartbeat age
- log age
- result file presence
- observed worker health

Use it when dispatcher summary is not enough.

## Ad Hoc Test And Review Tools

### Document review tool

Path:

- [review_doc.py](/Users/paul/projects/CENTRAL/scripts/review_doc.py)

Examples:

```bash
python3 scripts/review_doc.py --input docs/capability_memory_hld.md --mode hld
python3 scripts/review_doc.py --input docs/capability_memory_lld_01_schema.md --mode lld --context-level doc-only
python3 scripts/review_doc.py --input docs/capability_memory_lld_01_schema.md --mode lld --context-level targeted \
  --context-file docs/capability_memory_hld.md
```

Context levels:

- `doc-only`: review only the target doc
- `targeted`: review the target doc plus explicit context files
- `repo`: allow selective repo inspection

### Dispatcher kill-task test

Path:

- [test_dispatcher_kill_task.py](/Users/paul/projects/CENTRAL/tests/test_dispatcher_kill_task.py)

Use:

```bash
python3 -m unittest tests.test_dispatcher_kill_task
```

What it covers:

- operator `kill-task`
- failing active task without immediate retry
- failing queued task directly

### Worker log observability test

Path:

- [test_central_runtime_worker_status.py](/Users/paul/projects/CENTRAL/tests/test_central_runtime_worker_status.py)

Use:

```bash
python3 -m unittest tests.test_central_runtime_worker_status
```

What it covers:

- worker log growing/flat/stale signals
- human-readable worker status text

### Audit flow smoke

Path:

- [test_central_audit_flow.sh](/Users/paul/projects/CENTRAL/tests/test_central_audit_flow.sh)

Use:

```bash
bash tests/test_central_audit_flow.sh
```

What it covers:

- implementation task creation with paired audit
- implementation completion to audit eligibility
- audit completion closing the parent task

## Suggested Planner/Coordinator Loop

For a human or AI planner, the current practical loop is:

1. Check dispatcher state.

```bash
dispatcher status
dispatcher workers
```

2. Check canonical queue state.

```bash
python3 scripts/central_task_db.py view-eligible --json
python3 scripts/central_task_db.py view-review --json
python3 scripts/central_task_db.py task-list --json
```

3. Inspect one task deeply when needed.

```bash
python3 scripts/central_task_db.py task-show --task-id <TASK_ID> --json
```

4. Create or backfill new work through the helper.

```bash
python3 scripts/create_planner_task.py ...
```

5. Start or continue dispatcher execution.

```bash
dispatcher start --max-workers 1
```

6. Stop bad work explicitly if needed.

```bash
dispatcher kill-task <TASK_ID> --reason "..." --json
```

## Recommendation

For new planner/coordinator operators:

- start in CENTRAL
- trust the DB, not markdown
- use the helper for task creation
- use dispatcher status/workers as your runtime control surface
- use `task-show` for deep task inspection
- use the ad hoc tests above to validate operator/runtime behavior when unsure

Before onboarding someone onto this workflow from this exact checkout, first resolve the current merge-conflict markers in:

- [dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)
- [central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)
