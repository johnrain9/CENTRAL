# CENTRAL-OPS-08 Harden canonical task schema for machine parsing, prioritization, and DB extensibility

## Task Metadata

- `Task ID`: `CENTRAL-OPS-08`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Schema Reference`: [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)

## Objective

Refine the bootstrap markdown schema so it remains machine-ingestable only for transitional migration and compatibility work while the end-state model moves to SQLite.

## Context

- [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md) originally froze a bootstrap human-readable task model.
- `CENTRAL-OPS-09` changed the end-state direction so planner truth is DB-canonical rather than markdown-authored.
- Some transitional migration or compatibility tooling may still need strict parsing of legacy markdown task records.
- Any further work here should support migration and generated or export surfaces, not deepen markdown-first architecture.

## Scope Boundaries

- Define schema refinements needed for machine parsing and DB mapping.
- Freeze ownership semantics clearly enough for planner and worker assignment.
- Add fields needed for dispatch ordering, timestamps, and review/reconciliation lifecycle.
- Do not implement the ingestion bridge or database sync in this task.

## Deliverables

1. Amend bootstrap markdown guidance only if migration tooling still needs a strict machine-readable metadata contract.
2. Define explicit semantics for `Planner Owner` and `Worker Owner`, including allowed values and assignment expectations.
3. Add canonical fields for prioritization and freshness, at minimum covering dispatch ordering and timestamps.
4. Decide whether the lifecycle must expand beyond `todo`, `in_progress`, `blocked`, `done`, and document any review/reconciliation states required.
5. Document how canonical markdown fields map into the future autonomy/SQLite task records, including extensibility rules for additional metadata.
6. Update [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md) to reflect the hardened schema.

## Acceptance

1. Transitional migration tooling can parse bootstrap markdown task metadata without relying on ad hoc scraping heuristics.
2. A planner can explain the difference between planning ownership and current worker assignment unambiguously.
3. Dispatch order can be derived from canonical task data without relying on incidental file order in [`tasks.md`](/home/cobra/CENTRAL/tasks.md).
4. The schema supports future optional fields without breaking existing task files.
5. The documented mapping to runtime/SQLite state is concrete enough to guide `CENTRAL-OPS-03` and `CENTRAL-OPS-04`.

## Testing

- Manual review of [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- Manual review of [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md)
- Confirm at least one canonical task file can be interpreted using the hardened metadata contract alone
- Manual review complete on 2026-03-10 for:
  - transitional bootstrap markdown scope
  - DB-canonical supersession from `CENTRAL-OPS-09`
  - remaining value only for migration or compatibility tooling

## Dependencies

- `CENTRAL-OPS-01`
- `CENTRAL-OPS-02`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-08`.
- The worker reads this file as a transitional bootstrap task snapshot.
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
- Update this bootstrap task file first only while the markdown transition remains in place.
- Update [`tasks.md`](/home/cobra/CENTRAL/tasks.md) second.
- Update any bootstrap packet or reference doc only after this transitional record is correct.

## Validation Rules

- filename matches `CENTRAL-OPS-08`
- all required sections are present
- `Status` is one of `todo`, `in_progress`, `blocked`, `done`
- dependencies use stable identifiers or explicit external artifacts
