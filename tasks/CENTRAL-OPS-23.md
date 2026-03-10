# CENTRAL-OPS-23 Generate DB-native task-board landing page export

## Task Metadata

- `Task ID`: `CENTRAL-OPS-23`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `16`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Add a DB-generated landing-page export so operators can read a `tasks.md`-style portfolio board without reintroducing manual canonical markdown maintenance.

## Context

- `CENTRAL-OPS-16` implemented DB-generated views and basic markdown exports.
- `CENTRAL-OPS-19` retired manual canonical markdown maintenance as the primary workflow.
- There is still value in a human-readable board-style landing page, but it must be generated from DB state and clearly marked non-canonical.

## Scope Boundaries

- Implement generated landing-page export only.
- Do not make root-level `tasks.md` canonical again.
- Do not redesign the task schema or planner/runtime contracts.

## Deliverables

1. Add a CLI command that exports a `tasks.md`-style landing page from CENTRAL DB state.
2. Mark the output as generated and non-canonical.
3. Include portfolio summary and canonical CENTRAL task listings driven by DB state.
4. Document how operators regenerate the landing page.

## Acceptance

1. A single command can emit a readable landing-page markdown board from DB state.
2. The landing page clearly states it is generated and non-canonical.
3. Operators no longer need to hand-maintain a board-style markdown landing page for CENTRAL canonical tasks.

## Testing

- Manual review of the generated-board export implementation
- Manual review of the updated CLI documentation
- Manual review complete on 2026-03-10:
  - added `export-tasks-board-md` to [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - documented generated board export in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Dependencies

- Soft prerequisites: `CENTRAL-OPS-16`, `CENTRAL-OPS-19`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-23`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-23 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB remains the authoritative planning system.
- Generated landing pages remain export surfaces only.

## Validation Rules

- filename matches `CENTRAL-OPS-23`
- required sections are present
