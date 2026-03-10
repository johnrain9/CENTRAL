# CENTRAL-OPS-04 Implement CENTRAL-to-autonomy task ingestion bridge

## Task Metadata

- `Task ID`: `CENTRAL-OPS-04`
- `Status`: `done`
- `Target Repo`: `/home/cobra/photo_auto_tagging`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Bootstrap Packet`: [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md)

## Execution Settings

- `Priority`: `15`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `["/home/cobra/CENTRAL"]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Implement the bridge that reads canonical `CENTRAL` task files and creates or updates corresponding autonomy DB tasks without manual re-entry.

## Context

- The autonomy runtime already supports stable custom task IDs and DB dependency edges.
- The missing piece is an ingestion command that understands canonical CENTRAL task files.
- The bridge should sync definition fields without duplicating records on repeated runs.

## Scope Boundaries

- Implement a bridge command in the autonomy runtime.
- Keep sync one-way from `CENTRAL` to autonomy in this phase.
- Do not implement full closeout reconciliation back into markdown.

## Deliverables

1. A command that reads canonical `CENTRAL/tasks/*.md`.
2. Idempotent create or update behavior keyed by task ID.
3. Dependency sync for canonical task IDs.
4. Documentation of sync direction and conflict behavior.

## Acceptance

1. A canonical task in `CENTRAL` can become an autonomy task without manual duplication.
2. Re-running sync does not create duplicate autonomy tasks.
3. The bridge uses the canonical task file as the authored source of truth.

## Testing

- `cd /home/cobra/photo_auto_tagging && source .venv/bin/activate && autonomy central sync --central-root /home/cobra/CENTRAL --dry-run`
- `cd /home/cobra/photo_auto_tagging && source .venv/bin/activate && autonomy task list --json --status pending --profile default`

## Dependencies

- CENTRAL-OPS-01
- CENTRAL-OPS-03

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-04`.
- Implementation work belongs in `/home/cobra/photo_auto_tagging`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-04 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- `CENTRAL` task definition is authoritative.
- autonomy DB is the runtime mirror created by the bridge.
- repo-local boards are optional mirrors only.

## Validation Rules

- filename matches `CENTRAL-OPS-04`
- required sections are present
- bridge behavior matches [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)

## Architecture Note

- Completed as a transitional markdown-to-autonomy bridge.
- The long-term direction is now DB-canonical CENTRAL planning, so this implementation should be treated as migration scaffolding rather than final architecture.
