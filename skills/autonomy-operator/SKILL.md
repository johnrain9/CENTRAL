---
name: autonomy-operator
description: Use when operating the dispatcher and worker fleet, including status checks, throughput changes, worker inspection, review-aging checks, and routine run-control during daily execution.
---

# Autonomy Operator

Run from: `~/projects/CENTRAL`

Use this for daily dispatcher and worker-fleet operations.

## Primary Rule

Treat CENTRAL DB as the planner source of truth. Treat dispatcher and autonomy state as the execution surface.

## Main Commands

```bash
python3 scripts/dispatcher_control.py status
python3 scripts/dispatcher_control.py workers --json
python3 scripts/dispatcher_control.py logs
python3 scripts/dispatcher_control.py start
python3 scripts/dispatcher_control.py stop
python3 scripts/dispatcher_control.py restart
autonomy report summary --json --profile default
autonomy report review-aging --json --profile default
```

## Daily Rhythm

1. Check dispatcher state.
2. Inspect active workers before tailing logs.
3. Confirm configured concurrency and model defaults if throughput changed.
4. Review queue pressure and review-aging.
5. Run one cycle or keep the daemon healthy.
6. Use detailed logs only when worker status points to a suspect run.

## Notes

- Prefer `dispatcher ...` as the primary shell entrypoint when available.
- When a task’s scope or acceptance is unclear, verify it in CENTRAL DB rather than inferring from worker logs.
- Runtime worker result paths are the structured evidence surface; do not assume a separate report directory exists.
