# CENTRAL-OPS-11 Design DB-native CENTRAL/autonomy integration and retire markdown-first bridge assumptions

## Task Metadata

- `Task ID`: `CENTRAL-OPS-11`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: this file
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `7`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Replace the long-term architecture assumption that CENTRAL markdown syncs into autonomy, and define the DB-native integration model instead.

## Context

- `CENTRAL-OPS-03` and `CENTRAL-OPS-04` produced a valid transitional markdown-first bridge.
- That bridge should not define the steady-state system once CENTRAL moves to DB-canonical planning.
- The integration model now needs to start from CENTRAL structured task records.

## Scope Boundaries

- Define the steady-state integration model.
- Do not implement the final runtime integration in this task.

## Deliverables

1. Define whether autonomy uses CENTRAL DB directly, syncs from it, or shares a unified schema.
2. Define task/state mapping between CENTRAL planning state and autonomy runtime state.
3. Define API or CLI boundaries for planner actions vs dispatcher actions.
4. Define how existing markdown-bridge behavior is deprecated or retired.

## Acceptance

1. The steady-state integration model starts from DB-canonical CENTRAL state, not markdown file discovery.
2. Planner/runtime separation remains clear.
3. Transitional bridge behavior is explicitly marked as temporary.

## Testing

- Manual review of integration options and selected model.
- Demonstrate how a newly created canonical DB task becomes dispatchable.

## Dependencies

- `CENTRAL-OPS-09`
- `CENTRAL-OPS-10`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-11`.
- This file is the canonical bootstrap record for the integration-design task.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-11 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL is authoritative for this planning task.
- Update this file first, then generated summaries.

## Validation Rules

- filename matches `CENTRAL-OPS-11`
- required sections are present
