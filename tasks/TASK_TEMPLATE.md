# <TASK_ID> <task title>

## Task Metadata

- `Task ID`: `<TASK_ID>`
- `Status`: `todo`
- `Target Repo`: `</abs/path/or-canonical-repo-name>`
- `Task Type`: `<planning|implementation|ops|docs|migration>`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `<1-100>`
- `Task Kind`: `<read_only|mutating>`
- `Sandbox Mode`: `<workspace-write|danger-full-access|read-only|none>`
- `Approval Policy`: `<never|on-request|on-failure>`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

<one-paragraph desired outcome>

## Context

- <current state>
- <why this task exists>
- <important local constraints>

## Scope Boundaries

- <in-scope item>
- <out-of-scope item>

## Deliverables

1. <deliverable>
2. <deliverable>

## Acceptance

1. <worker can verify outcome>
2. <planner can verify ownership/source-of-truth behavior>

## Testing

- <command or manual check>
- <command or manual check>

## Dependencies

- <TASK_ID or explicit external dependency>

## Dispatch Contract

- Dispatch from `CENTRAL` using the task ID.
- Execute implementation work in `Target Repo`.
- Do not rely on repo-local task files for planner truth.
- Treat this markdown file as a transitional bootstrap or export surface until DB-native authoring lands.

## Closeout Contract

Required closeout line:

```text
<TASK_ID> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

Blocked rule:

- If blocked, include exactly one concrete unblocker request.

## Repo Reconciliation

- Update the canonical task file first.
- Update [`tasks.md`](/home/cobra/CENTRAL/tasks.md) second.
- Update any repo-local mirror only after CENTRAL is correct.

## Validation Rules

- filename matches `Task ID`
- all required sections are present
- `Status` is one of `todo`, `in_progress`, `blocked`, `done`
- every dependency uses a stable identifier or explicit external artifact
