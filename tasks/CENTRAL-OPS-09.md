# CENTRAL-OPS-09 Redesign CENTRAL canonical task system around SQLite as source of truth

## Task Metadata

- `Task ID`: `CENTRAL-OPS-09`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `5`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Redefine the CENTRAL task architecture so the canonical source of truth is a SQLite database rather than markdown task files.

## Context

- The markdown task-file model was useful for bootstrap but does not scale cleanly to hundreds of tasks, multiple planners, or high-throughput dispatch.
- The explicit direction is that CENTRAL should not depend on markdown or flat files as the canonical store.
- Planner truth, dependency edges, assignment state, and lifecycle metadata need structured storage from the start.

## Scope Boundaries

- Define the DB-canonical model and migration path.
- Do not implement the runtime DB in this task.

## Deliverables

1. Define the canonical SQLite schema for CENTRAL-owned tasks.
2. Define which markdown surfaces, if any, remain as generated views or exports.
3. Define migration rules from current markdown task files into DB records.
4. Update the high-level architecture docs to make DB-canonical planning explicit.

## Acceptance

1. The canonical source of truth is unambiguously the DB, not markdown files.
2. The schema supports hundreds of tasks with indexed queries and dependency traversal.
3. The migration path from current bootstrap markdown is concrete.

## Testing

- Manual review of the revised architecture docs.
- Demonstrate that every required task field has a DB home.
- Manual review complete on 2026-03-10 for:
  - [`docs/central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
  - [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
  - [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
- Review result:
  - accepted the DB-canonical direction
  - accepted generated markdown only as transitional or export surfaces
  - accepted that runtime integration must stop depending on markdown discovery

## Dependencies

- `CENTRAL-OPS-01`
- `CENTRAL-OPS-02`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-09`.
- This file is a transitional bootstrap record for the redesign task.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-09 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the target authoritative system after this redesign.
- Until DB-native authoring lands, treat this file as a transitional bootstrap snapshot and update generated summaries accordingly.

## Validation Rules

- filename matches `CENTRAL-OPS-09`
- required sections are present
