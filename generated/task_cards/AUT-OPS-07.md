# AUT-OPS-07 Debug dispatcher startup transaction nesting failure

Generated from CENTRAL DB at 2026-03-10T14:55:32+00:00. Do not edit manually.

## Metadata
- Task ID: `AUT-OPS-07`
- Planner Status: `todo`
- Runtime Status: `none`
- Priority: `3`
- Target Repo: `PHOTO_AUTO_TAGGING` (/home/cobra/photo_auto_tagging)
- Planner Owner: `planner/coordinator`
- Worker Owner: `unassigned`

## Objective
Restore reliable `dispatcher` startup in `/home/cobra/photo_auto_tagging` by identifying and fixing the transaction lifecycle bug that leaves SQLite inside an open transaction before `claim_next_task()` issues `BEGIN IMMEDIATE`, and add regression coverage for the failing startup/recovery path.

## Context
User-reported failure on 2026-03-10 when running `dispatcher`. Observed sequence: dispatcher starts, recovers orphan `RUN000001` for task `PQ-AI-01`, marks worker recovered/failed, then stops with `DispatcherError: failed to begin claim transaction: cannot start a transaction within a transaction`. Trace points to `/home/cobra/photo_auto_tagging/autonomy/storage.py:706` inside `claim_next_task()` during `self.conn.execute("BEGIN IMMEDIATE")`, called from `/home/cobra/photo_auto_tagging/autonomy/dispatch.py:387` via `_try_spawn_workers()`. Likely area is transaction handling around orphan recovery and claim flow in `autonomy/storage.py`, `autonomy/dispatch.py`, and any startup recovery logic that mutates state before the claim loop. The fix must preserve dispatcher correctness under orphan recovery, claim, and restart scenarios.

## Scope
In scope: reproduce the failure; inspect transaction boundaries in dispatcher startup, orphan recovery, and claim flow; implement a fix in `/home/cobra/photo_auto_tagging/autonomy/*`; add regression coverage for the failing path; verify `dispatcher` can start cleanly after orphan recovery. Out of scope: redesigning the overall dispatcher architecture, migrating the autonomy runtime to CENTRAL DB, or changing unrelated queue semantics.

## Deliverables
1. Root-cause analysis for why dispatcher startup enters `claim_next_task()` while already inside a transaction.
2. Code fix that prevents nested transaction failure while preserving orphan recovery and claim correctness.
3. Regression test(s) covering dispatcher startup with orphan recovery and subsequent claim attempt.
4. Brief operator note if behavior or recovery expectations change.

## Acceptance
1. `dispatcher` starts successfully in the previously failing scenario and does not crash with `cannot start a transaction within a transaction`.
2. Orphan recovery still marks stale or orphaned runs correctly before normal dispatch proceeds.
3. Regression test coverage exists for the recovered-orphan startup path and fails without the fix.
4. No new transaction-lifecycle errors appear in normal claim or restart flows.

## Testing
- Reproduce the reported startup failure before the fix if possible.
- Run targeted autonomy tests covering storage/dispatch transaction handling.
- Run a focused end-to-end dispatcher startup test that includes orphan recovery and at least one subsequent claim cycle.
- Verify `dispatcher status` or equivalent startup command succeeds after the fix.

## Dispatch
Dispatch from CENTRAL using `repo=CENTRAL do task AUT-OPS-07`. The worker should execute implementation work in `/home/cobra/photo_auto_tagging` and use the reported traceback as the starting failure case.

## Dependencies
- none

## Reconciliation
CENTRAL DB is the canonical planner store for this task. Reconcile worker outcomes in CENTRAL first. Because `/home/cobra/photo_auto_tagging` is currently a separately active repo, do not sweep unrelated changes; only reconcile the worker's scoped autonomy/dispatcher changes.
