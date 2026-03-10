# CENTRAL-OPS-01 Freeze canonical CENTRAL task schema and storage model

## Task Metadata

- `Task ID`: `CENTRAL-OPS-01`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: historical bootstrap snapshot only; DB-canonical model now supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Bootstrap Packet`: [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md)

## Execution Settings

- `Priority`: `10`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Define the canonical task format and on-disk layout for all future planner-owned work in `CENTRAL`.

## Context

- Current planning state is mixed across repo-local boards, `CENTRAL/tasks.md`, and task packet files.
- The operating decision is now explicit: `CENTRAL` owns canonical planner tasks.
- Workers need self-contained task bodies that do not depend on repo-local task files.

## Scope Boundaries

- Freeze the storage model and task schema.
- Define how the summary index points to canonical task bodies.
- Provide one example canonical task.
- Do not implement the full dispatcher sync or task migration in this task.

## Deliverables

1. Choose the canonical storage layout in `CENTRAL`.
2. Define the required task fields and status model.
3. Define how summary/index surfaces point to full task bodies.
4. Provide at least one example task in the new canonical format.

## Acceptance

1. A worker can execute `CENTRAL-OPS-01` from this file without needing a repo-local board.
2. A planner can tell unambiguously that this file is only a historical bootstrap record and not the long-term canonical DB record.
3. The chosen layout scales beyond a handful of repos and tasks.

## Testing

- Manual review of [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- Manual review of this file as the canonical example task
- Confirm `tasks.md` points back to this file

## Dependencies

- None

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-01`.
- The worker reads this file as a bootstrap task snapshot for historical context.
- Implementation work for this task stays in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-01 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

Planner closeout responsibilities:

- update [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- update [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md) while bootstrap packets still exist
- keep the canonical schema doc aligned with this example

## Repo Reconciliation

- `CENTRAL` is authoritative for this task.
- No repo-local mirror is required.
- Any future summary drift should be corrected in `CENTRAL` first.

## Validation Rules

- filename matches `CENTRAL-OPS-01`
- required sections are present
- `Status` uses the canonical lifecycle set
- [`tasks.md`](/home/cobra/CENTRAL/tasks.md) points back to this file
