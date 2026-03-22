---
name: autonomy-triage
description: Use when diagnosing failed runs, stale reviews, or stuck worker tasks and choosing an explicit next action such as approve, reject, reset, retry, or leave blocked.
---

# Autonomy Triage

Run from: `~/projects/CENTRAL`

Use this when a worker run failed, stalled, or is waiting on review and you need a disciplined decision.

## Primary Rule

Use CENTRAL DB to confirm intended scope and acceptance. Use autonomy and dispatcher artifacts as runtime evidence.

## Main Commands

```bash
autonomy report failures --json --profile default
autonomy report review-aging --json --profile default
autonomy task show TASK_ID --json --profile default
autonomy worker inspect WORKER_ID --json --profile default
autonomy worker tail WORKER_ID --profile default
autonomy task reset TASK_ID --profile default
autonomy worker retry WORKER_ID --profile default
autonomy task approve TASK_ID --reviewer "NAME" --profile default
autonomy task reject TASK_ID --reviewer "NAME" --notes "WHY" --profile default
```

## Decision Rules

- Approve when acceptance is met and evidence is concrete.
- Reject when correctness, evidence, or scope compliance is insufficient.
- Reset for transient environment failures before a clean rerun.
- Retry only when the prior run justifies another attempt.
- Leave blocked when dependencies or external decisions still prevent progress.

## Notes

- Keep reviewer decisions explicit and auditable.
- Do not use runtime state alone to redefine task scope.
- If a failure points to a planning defect, hand it back to planner workflow instead of repeatedly retrying.
