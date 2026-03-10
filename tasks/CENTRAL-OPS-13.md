# CENTRAL-OPS-13 Reconcile and re-scope transitional CENTRAL-OPS-05 through CENTRAL-OPS-08 under the DB-canonical model

## Task Metadata

- `Task ID`: `CENTRAL-OPS-13`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `9`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Clean up the remaining transitional CENTRAL planning tasks so `CENTRAL-OPS-05` through `CENTRAL-OPS-08` accurately reflect what is still needed, what is superseded, and what should be treated as migration-only work under the DB-canonical architecture.

## Context

- `CENTRAL-OPS-09` through `CENTRAL-OPS-12` changed the long-term architecture materially.
- Earlier bootstrap tasks still contain markdown-first assumptions, stale status text, or ambiguous scope.
- Before implementation starts, the planner backlog needs to stop carrying contradictory work items.

## Scope Boundaries

- Review and reconcile only `CENTRAL-OPS-05` through `CENTRAL-OPS-08` and their references.
- Do not implement DB runtime code in this task.

## Deliverables

1. Review `CENTRAL-OPS-05` through `CENTRAL-OPS-08` against the DB-canonical architecture.
2. Mark each task as one of: still needed, superseded, completed, or transitional-only.
3. Rewrite any remaining task text so it matches the DB-canonical direction.
4. Reconcile statuses and notes consistently across bootstrap task files, `tasks.md`, and `central_task_system_tasks.md`.

## Acceptance

1. No remaining `CENTRAL-OPS-05` through `CENTRAL-OPS-08` task contradicts DB-canonical planning.
2. Summary surfaces and bootstrap task files agree on current status and scope.
3. The implementation tranche can proceed without ambiguity about which bootstrap tasks are still relevant.

## Testing

- Manual review of `tasks.md`
- Manual review of `central_task_system_tasks.md`
- Manual review of [`tasks/CENTRAL-OPS-05.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-05.md), [`tasks/CENTRAL-OPS-06.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-06.md), [`tasks/CENTRAL-OPS-07.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-07.md), and [`tasks/CENTRAL-OPS-08.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-08.md) if present

## Dependencies

- `CENTRAL-OPS-09`
- `CENTRAL-OPS-10`
- `CENTRAL-OPS-11`
- `CENTRAL-OPS-12`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-13`.
- This file is a transitional bootstrap record for the reconciliation task.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-13 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the target authoritative system for planner truth.
- Until DB-native authoring lands, treat this file as a transitional bootstrap snapshot and update generated summaries accordingly.

## Validation Rules

- filename matches `CENTRAL-OPS-13`
- required sections are present
