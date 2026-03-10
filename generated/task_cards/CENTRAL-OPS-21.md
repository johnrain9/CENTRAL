# CENTRAL-OPS-21 Implement CENTRAL-native worker execution bridge

Generated from CENTRAL DB at 2026-03-10T14:55:32+00:00. Do not edit manually.

## Metadata
- Task ID: `CENTRAL-OPS-21`
- Planner Status: `todo`
- Runtime Status: `none`
- Priority: `5`
- Target Repo: `CENTRAL` (/home/cobra/CENTRAL)
- Planner Owner: `planner/coordinator`
- Worker Owner: `unassigned`

## Objective
Connect the CENTRAL-native dispatcher to actual worker execution. Once the daemon loop exists, this task should make claimed tasks launch the appropriate execution process, maintain heartbeats/progress, capture runtime evidence, and transition tasks through running, pending review, failed, timeout, and done states.

## Context
`CENTRAL-OPS-20` provides the daemon/orchestration loop, but a usable dispatcher still needs a worker execution adapter. Today worker execution is tied to the legacy autonomy runtime in `photo_auto_tagging`. The new DB-native system needs a compatible execution bridge that can spawn workers for claimed CENTRAL tasks and update CENTRAL runtime state correctly. This is the step that makes the new dispatcher actually do work instead of just polling and claiming.

## Scope
In scope: worker spawn/execution adapter, heartbeat/progress integration, runtime transition updates, artifact/result capture, and clean worker termination handling. Out of scope: replacing the actual worker implementation model beyond what is needed to launch and observe it correctly.

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

## Dispatch
Dispatch from CENTRAL using `repo=CENTRAL do task CENTRAL-OPS-21`. Implementation work belongs primarily in `/home/cobra/CENTRAL`; coordinate with worker runtime code only where the execution bridge truly requires it.

## Dependencies
- `CENTRAL-OPS-20` (todo) - Implement CENTRAL-native dispatcher daemon loop

## Reconciliation
CENTRAL DB is the canonical planner/runtime store for this task. Reconcile worker outcomes in CENTRAL first. Do not sweep unrelated changes in execution repos.
