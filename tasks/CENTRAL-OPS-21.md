# CENTRAL-OPS-21 Implement CENTRAL-native worker execution bridge

## Task Metadata

- `Task ID`: `CENTRAL-OPS-21`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `5`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `["/home/cobra/photo_auto_tagging"]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Connect the CENTRAL-native dispatcher to actual worker execution.

## Context

- `CENTRAL-OPS-20` provides the daemon/orchestration loop.
- A usable dispatcher still needs a worker execution adapter.
- Today worker execution is tied to the legacy autonomy runtime in `photo_auto_tagging`.

## Scope Boundaries

- Implement worker spawn/execution adapter, heartbeat/progress integration, runtime transition updates, artifact/result capture, and clean worker termination handling.
- Do not replace the actual worker implementation model beyond what is needed to launch and observe it correctly.

## Deliverables

1. Implement worker launch/execution from claimed CENTRAL tasks.
2. Wire runtime heartbeats and status transitions into the CENTRAL DB.
3. Capture artifacts/closeout evidence from worker runs.
4. Document how the CENTRAL-native dispatcher launches and monitors workers.

## Acceptance

1. A task claimed by the CENTRAL-native dispatcher can launch a real worker execution path.
2. Runtime state moves correctly through claimed/running/pending_review/failed/timeout/done based on worker outcomes.
3. Heartbeats and stale detection remain coherent while workers are active.
4. Worker outputs produce structured DB evidence or artifacts suitable for planner reconciliation.

## Testing

- Launch a real or test worker from a claimed CENTRAL task.
- Verify heartbeat updates while the worker is active.
- Verify success and failure paths record the expected runtime transitions.
- Verify artifacts or closeout evidence are captured in DB-linked form.

## Dependencies

- `CENTRAL-OPS-20`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-21`.
- Implementation work belongs primarily in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-21 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the canonical planner/runtime store for this task.
- Reconcile worker outcomes in CENTRAL first.

## Validation Rules

- filename matches `CENTRAL-OPS-21`
- required sections are present
