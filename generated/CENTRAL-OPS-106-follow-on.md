# Follow-on for CENTRAL-OPS-106

## Title

Raise `scripts/central_task_db.py` line coverage above 60% with additional in-process tests

## Reason

`CENTRAL-OPS-106` added direct coverage for operator commands, runtime transitions, stale-lease recovery, and bootstrap migration paths, but the broader validation run still measured `scripts/central_task_db.py` at 39% coverage.

## Gap

- large planner/export/reporting surfaces remain lightly exercised in-process
- subprocess-heavy validation patterns do not materially improve the in-process coverage measurement

## Next Work

1. Add in-process tests for planner CRUD/reporting command functions that currently rely on subprocess-based smoke checks.
2. Add direct tests for export, dependency, repo/capability, and planner panel code paths that are still mostly uncovered.
3. Re-run a full `central_task_db` coverage pass and verify the file exceeds 60%.
