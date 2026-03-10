# CENTRAL-OPS-20 Implement CENTRAL-native dispatcher daemon loop

Generated from CENTRAL DB at 2026-03-10T14:55:32+00:00. Do not edit manually.

## Metadata
- Task ID: `CENTRAL-OPS-20`
- Planner Status: `todo`
- Runtime Status: `none`
- Priority: `4`
- Target Repo: `CENTRAL` (/home/cobra/CENTRAL)
- Planner Owner: `planner/coordinator`
- Worker Owner: `unassigned`

## Objective
Create the first real CENTRAL-native dispatcher loop so DB-native task/runtime state is not just a set of primitives. The daemon should own polling, claim attempts, active-run tracking, periodic stale-lease recovery, and operator-visible lifecycle logging against the CENTRAL DB.

## Context
CENTRAL already has canonical DB schema, planner CRUD, runtime claim/heartbeat/transition commands, and operator views in `scripts/central_task_db.py`, but there is no long-running daemon equivalent to `autonomy dispatch daemon`. Today the `dispatcher` shell wrapper still launches the legacy autonomy runtime from `photo_auto_tagging`. To use CENTRAL as the real control plane, a native daemon loop must exist first. This task should implement the daemon skeleton and control flow, not the full worker execution adapter. Worker spawning/execution details belong in the next task.

## Scope
In scope: daemon loop, polling cadence, active-set bookkeeping, DB-native eligibility checks, claim attempts, stale-lease recovery scheduling, operator logs/status hooks, and clean shutdown behavior. Out of scope: spawning Codex workers or executing task payloads end-to-end; that belongs in the worker execution bridge task.

## Deliverables
1. Implement a CENTRAL-native dispatcher daemon command or module.
2. Add loop logic for eligible-query polling, claim attempts, active runtime tracking, and periodic stale recovery.
3. Add operator-visible logging and status surfaces for the daemon.
4. Document daemon startup, shutdown, and runtime behavior.

## Acceptance
1. A CENTRAL-native daemon can start, stay running, and interact with the CENTRAL DB without using `autonomy dispatch daemon`.
2. The daemon can detect eligible work, attempt claims, and run periodic stale-recovery logic without crashing.
3. Operator-visible status/logging exists for startup, cycles, and shutdown.
4. The daemon loop is structured cleanly enough for worker execution to plug in without re-architecting it.

## Testing
- Start the daemon against a test DB and verify it stays alive across multiple cycles.
- Verify it logs startup, cycle activity, and shutdown cleanly.
- Verify stale-recovery logic can run on schedule without breaking the loop.
- Verify it does not require the legacy autonomy dispatcher to function.

## Dispatch
Dispatch from CENTRAL using `repo=CENTRAL do task CENTRAL-OPS-20`. Implementation work belongs in `/home/cobra/CENTRAL`.

## Dependencies
- none

## Reconciliation
CENTRAL DB is the canonical planner/runtime store for this task. Reconcile worker outcomes in CENTRAL first. Do not touch `photo_auto_tagging` runtime code unless needed for compatibility notes only.
