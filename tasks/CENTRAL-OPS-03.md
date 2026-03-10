# CENTRAL-OPS-03 Re-root dispatcher operating model to CENTRAL-owned tasks

## Task Metadata

- `Task ID`: `CENTRAL-OPS-03`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Bootstrap Packet`: [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md)

## Execution Settings

- `Priority`: `20`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Define how the dispatcher and autonomy task system consume `CENTRAL`-owned task definitions without treating repo-local boards as execution truth.

## Context

- Canonical planner tasks now live in `CENTRAL/tasks/`.
- The autonomy runtime still executes from the DB in `photo_auto_tagging`.
- The missing contract was the handoff layer between CENTRAL-authored tasks and autonomy runtime state.

## Scope Boundaries

- Define the integration model and field mapping.
- Define status authority and reconciliation rules.
- Do not implement the bridge logic in this task.

## Deliverables

1. A documented integration model between `CENTRAL` and autonomy DB.
2. Explicit task ID, dependency, and execution-policy mapping rules.
3. A clear answer for dispatcher discovery.

## Acceptance

1. A planner can explain how CENTRAL-authored tasks become dispatcher-visible work.
2. The model states which system is authoritative for task definition versus runtime execution.
3. Repo targeting and writable-dir derivation are explicit.

## Testing

- Manual review of [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
- Confirm the doc defines dispatcher discovery, ID mapping, status mapping, and reconciliation

## Dependencies

- CENTRAL-OPS-01

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-03`.
- This file is a transitional bootstrap task snapshot for the DB-native integration redesign.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-03 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- `CENTRAL` is authoritative.
- There is no repo-local mirror requirement for this planning task.

## Validation Rules

- filename matches `CENTRAL-OPS-03`
- required sections are present
- [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md) remains aligned with the bridge behavior

## Architecture Note

- Completed under the bootstrap markdown-first model.
- The long-term direction is now DB-canonical CENTRAL planning, so this task should be read as transitional architecture only.
