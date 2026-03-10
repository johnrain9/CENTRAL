# CENTRAL-OPS-17 Implement DB-native dispatcher and runtime state integration

## Task Metadata

- `Task ID`: `CENTRAL-OPS-17`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `13`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `["/home/cobra/photo_auto_tagging"]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Implement the DB-native runtime path for dispatcher discovery, task claim/lease management, heartbeats, stale-lease recovery, and runtime status transitions.

## Context

- `CENTRAL-OPS-10` and `CENTRAL-OPS-11` defined the concurrency and integration model.
- The old markdown bridge is transitional and should not define steady-state runtime behavior.
- Dispatcher/runtime logic now needs a concrete DB-native execution path.

## Scope Boundaries

- Implement runtime-owned DB tables, queries, and control-plane integration for dispatcher behavior.
- Do not implement planner CRUD or migration/import in this task.

## Deliverables

1. Implement DB-native eligibility queries for dispatcher use.
2. Implement atomic claim/lease creation, heartbeat renewal, and stale-lease recovery.
3. Implement runtime status transitions including review/failure/timeout handling.
4. Document how dispatcher/runtime actions interact with planner-owned state.

## Acceptance

1. Dispatcher can discover and claim eligible work from DB-native state without markdown file discovery.
2. Double-claim protection and stale-lease handling work according to the concurrency contract.
3. Runtime state transitions are queryable from structured DB tables.

## Testing

- Simulate eligible task discovery and claim flow
- Simulate heartbeat renewal and stale lease recovery
- Simulate runtime transitions into running, pending review, failed, timeout, and done
- Manual review complete on 2026-03-10:
  - DB-native eligibility, claim, heartbeat, transition, and stale-recovery commands implemented in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - runtime/operator command surface documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

Review result:
- accepted DB-native lease and runtime-state mutations as the steady-state dispatcher control-plane contract
- accepted runtime status living in CENTRAL DB rather than markdown discovery or sync state

## Dependencies

- `CENTRAL-OPS-14`
- `CENTRAL-OPS-10`
- `CENTRAL-OPS-11`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-17`.
- Implementation work belongs primarily in `/home/cobra/CENTRAL`; coordinate with `/home/cobra/photo_auto_tagging` only if runtime integration code still lives there.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-17 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is authoritative for planner truth; runtime-owned tables live alongside it or attach cleanly through the selected integration path.
- Update this bootstrap task file and generated summaries after implementation.
- Implementation now lives primarily in:
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Validation Rules

- filename matches `CENTRAL-OPS-17`
- required sections are present
