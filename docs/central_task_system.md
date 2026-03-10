# CENTRAL Canonical Task System

This document defines the canonical CENTRAL task model: storage layout, required fields, lifecycle and status rules, dependency encoding, dispatch and closeout contract, validation rules, and naming and index rules.

## Canonical Layout

Use a hybrid layout:

- [`tasks.md`](/home/cobra/CENTRAL/tasks.md): human-readable index, portfolio summary, and quick status board
- `tasks/<TASK_ID>.md`: canonical self-contained task body
- task packet files such as [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md): temporary bootstrap/context packs only, not long-term source of truth

Why this layout:

- one large file does not scale across repos
- per-task files give workers a stable dispatch target
- `tasks.md` remains fast to scan for portfolio status and prioritization

## Source Of Truth

- For planner-owned canonical tasks, `tasks/<TASK_ID>.md` is the source of truth.
- [`tasks.md`](/home/cobra/CENTRAL/tasks.md) is the summary index and portfolio surface, not the canonical body.
- Repo-local boards are mirrors or local intake only once a task has a canonical file in `CENTRAL`.
- If `CENTRAL` and a repo-local board disagree for a canonical task, `CENTRAL` wins and the planner reconciles the mirror.

## Required Fields

Every canonical task file under `tasks/` must include these sections:

- `Task Metadata`
- `Objective`
- `Context`
- `Scope Boundaries`
- `Deliverables`
- `Acceptance`
- `Testing`
- `Dependencies`
- `Dispatch Contract`
- `Closeout Contract`
- `Repo Reconciliation`
- `Validation Rules`

`Task Metadata` must include:

- `Task ID`
- `Status`
- `Target Repo`
- `Task Type`
- `Planner Owner`
- `Worker Owner`
- `Source Of Truth`
- `Summary Record`

Allowed `Status` values:

- `todo`
- `in_progress`
- `blocked`
- `done`

Status meanings:

- `todo`: defined and not actively being worked
- `in_progress`: a worker or planner is actively executing it
- `blocked`: cannot make useful progress because of one concrete blocker
- `done`: acceptance is met and closeout is recorded

## Edit Policy

- Canonical task files are editable in place by the planner or coordinator.
- The task body is not append-only.
- Closeout evidence belongs in `Closeout Contract` and any later execution-history section if added by convention.
- Material status, dependency, or scope updates should update the canonical file first and then the summary index.

## Dependency Encoding

- Dependencies are represented as a flat markdown list of task IDs under `Dependencies`.
- Each entry should use the stable canonical ID, for example `CENTRAL-OPS-01` or `AUT-OPS-06`.
- If a dependency is external to the canonical task system, name the concrete artifact or repo-local task ID explicitly.
- A task is eligible only when all listed dependencies are done.

## Index Rules

`tasks.md` is the summary index, not the canonical task body.

For each planner-owned canonical task tracked there:

- keep the task ID and status in the summary list
- add a link to the canonical file when the task is migrated to the new format
- keep short scope/why lines in the index
- do not duplicate the full task body in `tasks.md`

## Dispatch Contract

Workers should be able to execute a task from the canonical file alone.

Dispatch shape:

```text
repo=CENTRAL do task CENTRAL-OPS-01
```

Execution rule:

- the dispatch prompt references the task ID from `CENTRAL`
- the worker opens `CENTRAL/tasks/<TASK_ID>.md`
- `Target Repo` inside that file tells the worker where implementation work belongs

## Closeout Contract

Required closeout line:

```text
<TASK_ID> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

Closeout requirements:

- `done` requires tests or a concrete manual-review result
- `blocked` requires exactly one concrete unblocker statement
- `ref` must include a branch, commit, file path, or equivalent implementation reference
- planner reconciles the canonical file and summary index after closeout

## Validation Rules

- filename must exactly match `Task ID`
- `Status` must be one of `todo`, `in_progress`, `blocked`, `done`
- `Target Repo` must be an absolute path or a canonical repo name that resolves unambiguously
- all required sections must exist
- `Summary Record` must point to the summary index
- every dependency entry must use a stable task identifier or explicit external artifact reference

## Dispatcher Discovery

- The dispatcher does not discover work from repo-local boards in the canonical model.
- The future CENTRAL-to-autonomy bridge reads canonical task files in `CENTRAL/tasks/`.
- Discovery should consider only tasks whose canonical `Status` is `todo` and whose dependencies are satisfied.
- [`tasks.md`](/home/cobra/CENTRAL/tasks.md) is not the source for eligibility decisions; it is the human-readable index.

## Repo Reconciliation

- For canonical tasks, CENTRAL status is authoritative.
- Repo-local boards may keep a mirrored summary entry for humans working inside a target repo.
- When implementation reality changes first in the target repo, the planner updates the canonical CENTRAL task before updating any mirror.
- Reconciliation must happen in the same work session that discovers drift.

## File Naming

- one task per file
- filename must exactly match the task ID, for example `CENTRAL-OPS-01.md`
- task IDs remain stable even if the title changes

## Relationship To Repo-Local Boards

- During migration, repo-local boards may still exist as mirrors or local intake.
- Canonical planner-owned execution tasks live in `CENTRAL/tasks/`.
- Repo-local boards should not be required to understand the full task body once a task is canonicalized in `CENTRAL`.

## Example

Canonical example task:

- [`tasks/CENTRAL-OPS-01.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-01.md)

Reusable template:

- [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md)

The example task demonstrates the schema in a real instance. The template is the reusable starting point for future canonical tasks.
