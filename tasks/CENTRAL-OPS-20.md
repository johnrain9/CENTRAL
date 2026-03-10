# CENTRAL-OPS-20 Implement CENTRAL-native dispatcher daemon loop

## Task Metadata

- `Task ID`: `CENTRAL-OPS-20`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `4`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Create the first real CENTRAL-native dispatcher loop so DB-native task/runtime state is not just a set of primitives.

## Context

- CENTRAL has DB schema, planner CRUD, runtime claim/heartbeat/transition commands, and operator views in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py).
- There is no long-running daemon equivalent to `autonomy dispatch daemon` in CENTRAL yet.
- Today `dispatcher` still routes to the legacy runtime path.

## Scope Boundaries

- Implement daemon loop, polling cadence, active-set bookkeeping, DB-native eligibility checks, claim attempts, stale-lease recovery scheduling, operator logs/status hooks, and clean shutdown behavior.
- Do not implement full worker execution in this task.

## Deliverables

1. Implement a CENTRAL-native dispatcher daemon command or module.
2. Add loop logic for eligible-query polling, claim attempts, active runtime tracking, and periodic stale recovery.
3. Add operator-visible logging and status surfaces for the daemon.
4. Document daemon startup, shutdown, and runtime behavior.

## Acceptance

1. A CENTRAL-native daemon can start, stay running, and interact with the CENTRAL DB without using `autonomy dispatch daemon`.
2. The daemon can detect eligible work, attempt claims, and run periodic stale-recovery logic without crashing.
3. Operator-visible status/logging exists for startup, cycles, and shutdown.
4. The daemon loop is structured cleanly enough for worker execution to plug in without re-architecting it.

## Testing

- Start the daemon against a test DB and verify it stays alive across multiple cycles.
- Verify it logs startup, cycle activity, and shutdown cleanly.
- Verify stale-recovery logic can run on schedule without breaking the loop.
- Verify it does not require the legacy autonomy dispatcher to function.

## Dependencies

- Soft prerequisite: `CENTRAL-OPS-17`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-20`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-20 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the canonical planner/runtime store for this task.
- Reconcile worker outcomes in CENTRAL first.

## Validation Rules

- filename matches `CENTRAL-OPS-20`
- required sections are present
