# CENTRAL-OPS-16 Implement DB-generated operator views and exports

## Task Metadata

- `Task ID`: `CENTRAL-OPS-16`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `12`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Build the CLI/JSON/dashboard read models and any optional markdown exports that surface CENTRAL DB task state without becoming a second source of truth.

## Context

- `CENTRAL-OPS-12` defined the generated-view contract.
- Operators need real surfaces for summary, eligible, blocked, assignments, review, and task detail.
- These surfaces must read from DB state, not hand-maintained files.

## Scope Boundaries

- Implement generated views and export surfaces only.
- Do not implement planner CRUD or dispatcher runtime claim logic in this task.

## Deliverables

1. Implement required CLI and JSON views for summary, eligible, blocked, per-repo, assignments, review, and task detail.
2. Implement optional markdown export generation only where useful, clearly marked non-canonical.
3. Add freshness/non-canonical markers to generated outputs.
4. Document how operators regenerate or query these views.

## Acceptance

1. Operators can answer the key portfolio and queue questions from DB-generated views.
2. Generated outputs are clearly marked non-canonical.
3. The system does not require a giant manually maintained `tasks.md` to operate.

## Testing

- Populate sample DB records and verify each required view renders correctly
- Verify freshness and source banners appear in generated outputs
- Verify optional markdown exports can be regenerated from DB state

## Dependencies

- `CENTRAL-OPS-14`
- `CENTRAL-OPS-12`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-16`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-16 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is authoritative; views are derived outputs.
- Update this bootstrap task file and any generated summaries after implementation.

## Validation Rules

- filename matches `CENTRAL-OPS-16`
- required sections are present
