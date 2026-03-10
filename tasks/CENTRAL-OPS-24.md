# CENTRAL-OPS-24 Add one-shot markdown export bundle generation

## Task Metadata

- `Task ID`: `CENTRAL-OPS-24`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `17`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Add a one-shot command that regenerates the standard non-canonical markdown export bundle from CENTRAL DB state so operators do not need to run several export commands manually.

## Context

- `CENTRAL-OPS-16` added generated views and individual markdown exports.
- `CENTRAL-OPS-23` added a generated board-style landing page export.
- Operators still benefit from a single refresh command for all standard markdown surfaces, but those surfaces must remain derived outputs only.

## Scope Boundaries

- Implement bundle generation for existing markdown export surfaces.
- Do not reintroduce markdown as canonical planner state.
- Do not change planner/runtime ownership rules.

## Deliverables

1. Add a one-shot CLI command that writes the standard markdown export bundle.
2. Include board, summary, blocked, review, assignments, and per-task card exports.
3. Keep all outputs clearly marked generated and non-canonical.
4. Document the bundle command for operator use.

## Acceptance

1. Operators can refresh the standard markdown export bundle with one command.
2. Exported markdown remains clearly generated and non-canonical.
3. The bundle command reuses DB state rather than any markdown-to-markdown transform.

## Testing

- Manual review of the bundle export implementation
- Manual review of the updated CLI documentation
- Manual review complete on 2026-03-10:
  - added `export-markdown-bundle` to [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - documented bundle generation in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Dependencies

- Soft prerequisites: `CENTRAL-OPS-16`, `CENTRAL-OPS-23`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-24`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-24 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB remains the authoritative planning system.
- Markdown export bundles remain derived outputs only.

## Validation Rules

- filename matches `CENTRAL-OPS-24`
- required sections are present
