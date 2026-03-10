# CENTRAL-OPS-08 Harden canonical task schema for machine parsing, prioritization, and DB extensibility

## Task Metadata

- `Task ID`: `CENTRAL-OPS-08`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: this file
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Schema Reference`: [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)

## Objective

Refine the canonical CENTRAL task schema so it is both human-maintainable and reliably machine-ingestable for the future CENTRAL-to-autonomy bridge and SQLite-backed runtime state.

## Context

- [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md) now defines the canonical human-readable task model.
- The current schema is good enough for planner use, but it still has gaps for automated ingestion and durable runtime state.
- The next bridge work will need stable parsing, dispatch ordering, clear ownership semantics, and clean mapping into database fields.
- The intended operating model is authored source in `CENTRAL`, with execution/runtime state mirrored into the autonomy system rather than planning directly in raw SQL.

## Scope Boundaries

- Define schema refinements needed for machine parsing and DB mapping.
- Freeze ownership semantics clearly enough for planner and worker assignment.
- Add fields needed for dispatch ordering, timestamps, and review/reconciliation lifecycle.
- Do not implement the ingestion bridge or database sync in this task.

## Deliverables

1. Amend [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md) with a machine-readable metadata contract, such as YAML front matter or an equally strict structured header.
2. Define explicit semantics for `Planner Owner` and `Worker Owner`, including allowed values and assignment expectations.
3. Add canonical fields for prioritization and freshness, at minimum covering dispatch ordering and timestamps.
4. Decide whether the lifecycle must expand beyond `todo`, `in_progress`, `blocked`, `done`, and document any review/reconciliation states required.
5. Document how canonical markdown fields map into the future autonomy/SQLite task records, including extensibility rules for additional metadata.
6. Update [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md) to reflect the hardened schema.

## Acceptance

1. A bridge implementation can parse canonical task metadata without relying on ad hoc markdown scraping heuristics.
2. A planner can explain the difference between planning ownership and current worker assignment unambiguously.
3. Dispatch order can be derived from canonical task data without relying on incidental file order in [`tasks.md`](/home/cobra/CENTRAL/tasks.md).
4. The schema supports future optional fields without breaking existing task files.
5. The documented mapping to runtime/SQLite state is concrete enough to guide `CENTRAL-OPS-03` and `CENTRAL-OPS-04`.

## Testing

- Manual review of [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- Manual review of [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md)
- Confirm at least one canonical task file can be interpreted using the hardened metadata contract alone

## Dependencies

- `CENTRAL-OPS-01`
- `CENTRAL-OPS-02`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-08`.
- The worker reads this file as the canonical task body.
- Implementation work for this task stays in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-08 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

Blocked rule:

- If blocked, include exactly one concrete unblocker request.

## Repo Reconciliation

- `CENTRAL` is authoritative for this task.
- Update this canonical task file first.
- Update [`tasks.md`](/home/cobra/CENTRAL/tasks.md) second.
- Update any bootstrap packet or reference doc only after the canonical record is correct.

## Validation Rules

- filename matches `CENTRAL-OPS-08`
- all required sections are present
- `Status` is one of `todo`, `in_progress`, `blocked`, `done`
- dependencies use stable identifiers or explicit external artifacts
