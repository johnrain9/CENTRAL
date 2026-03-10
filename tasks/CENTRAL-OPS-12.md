# CENTRAL-OPS-12 Define generated views and operator surfaces for DB-canonical task management

## Task Metadata

- `Task ID`: `CENTRAL-OPS-12`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `8`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Define which human-facing views should be generated from the canonical DB so operators and planners can scan the portfolio without making those views the source of truth.

## Context

- Once DB is canonical, flat files should become derived views, not manually maintained records.
- Operators still need readable summaries, dashboards, and possibly exported task cards for worker handoff.

## Scope Boundaries

- Define operator-facing generated views.
- Do not implement dashboards or generation code in this task.

## Deliverables

1. Define required generated views such as portfolio summary, per-repo queue, blocked tasks, and worker assignments.
2. Define whether `tasks.md` remains as a generated artifact or is replaced by another operator surface.
3. Define any exported task-card format for workers when a human-readable handoff is useful.
4. Define refresh/update rules for generated views.

## Acceptance

1. Operators can scan task state without editing generated surfaces manually.
2. Generated views are clearly non-canonical.
3. The design supports hundreds of tasks without requiring people to read a giant flat file.

## Testing

- Manual review of proposed views and refresh model.
- Demonstrate that critical operator questions can be answered from generated views.
- Manual review complete on 2026-03-10 for:
  - [`docs/central_generated_views.md`](/home/cobra/CENTRAL/docs/central_generated_views.md)
- Review result:
  - accepted `tasks.md` as a generated landing page rather than canonical board
  - accepted dedicated generated views for eligibility, blocked work, assignments, review, and per-task handoff cards
  - accepted explicit freshness and non-canonical marking rules for all generated artifacts

## Dependencies

- `CENTRAL-OPS-09`
- `CENTRAL-OPS-10`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-12`.
- This file is a transitional bootstrap record for the generated-views task.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-12 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL is authoritative for this planning task.
- Update this file first, then generated summaries.
- Long-term generated markdown and CLI views should derive from DB state, not this bootstrap snapshot.

## Validation Rules

- filename matches `CENTRAL-OPS-12`
- required sections are present
- generated-view design remains aligned with DB-canonical ownership
