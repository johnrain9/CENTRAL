# CENTRAL-OPS-15 Implement planner-facing DB CRUD and reconciliation commands

## Task Metadata

- `Task ID`: `CENTRAL-OPS-15`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `11`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Implement planner-facing commands or APIs that create, update, prioritize, assign, and reconcile canonical tasks directly in the CENTRAL DB.

## Context

- The DB foundation must exist before planner workflows can stop relying on markdown edits.
- Planner-owned operations need clear boundaries from runtime-owned operations.
- This is the control plane for future planner AI usage.

## Scope Boundaries

- Implement planner-side CRUD and reconciliation only.
- Do not implement worker claim/lease runtime behavior in this task.

## Deliverables

1. Create planner-facing task create/update commands or APIs against the CENTRAL DB.
2. Implement dependency management, priority updates, owner assignment, and status transitions for planner lifecycle.
3. Implement planner-side closeout reconciliation commands for done/blocked outcomes.
4. Document command usage for planner operation.

## Acceptance

1. A planner can create and modify canonical tasks without editing markdown files.
2. Planner lifecycle state, dependencies, and ownership can be updated through structured commands.
3. Closeout reconciliation can be performed against the DB without manual SQL.

## Testing

- Create a test task in the DB
- Update its priority, dependencies, and ownership
- Reconcile a closeout outcome and verify DB state changes as expected
- Manual review complete on 2026-03-10:
  - planner-facing create, update, list, show, and reconcile commands implemented in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - command usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

Review result:
- accepted DB-native planner CRUD and reconciliation as the canonical planner control plane
- accepted optimistic planner version checks and active-lease guardrails for planner updates

## Dependencies

- `CENTRAL-OPS-14`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-15`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-15 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the authoritative system for planner truth.
- Update this bootstrap task file and any generated summaries after implementation.
- Implementation now lives in:
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Validation Rules

- filename matches `CENTRAL-OPS-15`
- required sections are present
