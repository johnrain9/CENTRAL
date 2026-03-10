# CENTRAL-OPS-10 Define multi-planner and multi-worker concurrency model for dispatcher scale

## Task Metadata

- `Task ID`: `CENTRAL-OPS-10`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `planning`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `6`
- `Task Kind`: `read_only`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Define claim, assignment, scheduling, and reconciliation rules that remain correct when multiple planner AIs and multiple workers operate concurrently.

## Context

- The target operating model includes multiple planners scheduling work for multiple workers around the clock.
- A single-user or single-planner task model will break under that load.
- Concurrency, locking, ownership, queue fairness, and stale-claim recovery must be designed intentionally.

## Scope Boundaries

- Define concurrency semantics and failure handling.
- Do not implement locking or scheduler code in this task.

## Deliverables

1. Define planner vs worker write responsibilities.
2. Define claim/lease semantics for workers and planners.
3. Define conflict rules for concurrent planner edits.
4. Define stale claim, retry, timeout, and reassignment handling.
5. Define dispatch fairness or prioritization policy across repos and worker capacity.

## Acceptance

1. The model prevents double-dispatch and ambiguous ownership.
2. Planner concurrency rules are concrete enough to implement safely.
3. Recovery from abandoned work is defined.

## Testing

- Manual review of concurrency scenarios and failure cases.
- Walk through at least three races: double claim, planner conflict, stale worker lease.
- Manual review complete on 2026-03-10 for:
  - [`docs/central_task_concurrency.md`](/home/cobra/CENTRAL/docs/central_task_concurrency.md)
  - race outcomes for double claim, planner conflict, and stale worker lease

## Dependencies

- `CENTRAL-OPS-09`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-10`.
- This file is a transitional bootstrap record for the concurrency-model task.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-10 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the target authoritative system for this planning contract.
- Until DB-native authoring lands, treat this file as a transitional bootstrap snapshot and update generated summaries accordingly.

## Validation Rules

- filename matches `CENTRAL-OPS-10`
- required sections are present
