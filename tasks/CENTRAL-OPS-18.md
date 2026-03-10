# CENTRAL-OPS-18 Migrate bootstrap CENTRAL task records into the canonical DB

## Task Metadata

- `Task ID`: `CENTRAL-OPS-18`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `migration`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `14`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Import or migrate the current bootstrap CENTRAL task records into the canonical DB so live planning can stop depending on markdown-maintained state.

## Context

- The DB exists only once `CENTRAL-OPS-14` lands.
- Current task definitions and summaries still live in bootstrap markdown surfaces.
- Migration must preserve task identity and enough history to keep planning continuity.

## Scope Boundaries

- Migrate bootstrap CENTRAL planning records into DB state.
- Do not retire the markdown bridge completely in this task.

## Deliverables

1. Implement a migration/import path from bootstrap CENTRAL task files and relevant packet surfaces into the DB.
2. Preserve stable `task_id` values and key metadata during migration.
3. Record migration provenance so imported records can be audited.
4. Document the migration procedure and rollback considerations.

## Acceptance

1. Existing bootstrap CENTRAL tasks appear in the canonical DB with stable IDs.
2. Migration can be audited and does not silently duplicate task records.
3. Planning can begin reading live task state from the DB after migration.

## Testing

- Run migration against a representative bootstrap task set
- Verify stable IDs and critical fields in DB output
- Re-run migration and confirm duplicate-safe behavior
- Manual review complete on 2026-03-10:
  - bootstrap migration/import path implemented as `migrate-bootstrap` in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - migration procedure and rollback guidance documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

Review result:
- accepted bootstrap import from task files plus packet-only records as sufficient cutover scaffolding
- accepted duplicate-safe skip behavior as the default migration posture

## Dependencies

- `CENTRAL-OPS-13`
- `CENTRAL-OPS-14`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-18`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-18 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB becomes the live planning store after successful migration.
- Any remaining bootstrap markdown should be treated as import/export or archival material only.
- Implementation now lives in:
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Validation Rules

- filename matches `CENTRAL-OPS-18`
- required sections are present
