# CENTRAL-OPS-14 Implement the canonical CENTRAL SQLite task database and migration scaffold

## Task Metadata

- `Task ID`: `CENTRAL-OPS-14`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `10`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Create the real CENTRAL SQLite task database, migrations, and bootstrap tooling so planner truth can move out of markdown and into structured storage.

## Context

- `CENTRAL-OPS-09` defined the DB-canonical architecture.
- `CENTRAL-OPS-10` and `CENTRAL-OPS-11` defined concurrency and runtime integration expectations.
- No actual canonical DB implementation exists yet in `CENTRAL`.

## Scope Boundaries

- Implement the DB schema, migration runner, and bootstrap/init path.
- Do not implement full planner CRUD, generated views, or dispatcher runtime behavior in this task.

## Deliverables

1. Create the SQLite schema and migration files for the canonical CENTRAL task DB.
2. Add a bootstrap/init command that creates or upgrades the DB safely.
3. Add minimal repo/config plumbing so tools can locate the canonical DB reliably.
4. Document how the DB is initialized and where it lives.

## Acceptance

1. A fresh CENTRAL checkout can initialize the canonical task DB with one command.
2. The implemented schema matches the DB design docs closely enough for later CRUD and runtime work.
3. Schema upgrades are handled by explicit migrations rather than ad hoc file replacement.

## Testing

- Initialize the DB in a clean or temporary location
- Verify the expected tables exist
- Run the migration command twice and confirm idempotent behavior

## Dependencies

- `CENTRAL-OPS-09`
- `CENTRAL-OPS-10`
- `CENTRAL-OPS-11`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-14`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-14 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the target authoritative system for planner truth.
- Until DB-native authoring lands fully, update this bootstrap task file and generated summaries after implementation.

## Validation Rules

- filename matches `CENTRAL-OPS-14`
- required sections are present
