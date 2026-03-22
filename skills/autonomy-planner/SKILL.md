---
name: autonomy-planner
description: Use when turning planning intent into executable dispatcher or CENTRAL tasks with explicit dependencies, especially for canonical planning, dependency encoding, task drafting, or legacy autonomy-queue compatibility work.
---

# Autonomy Planner

Run from: `~/projects/CENTRAL`

Use this for planner work that turns design or roadmap intent into executable tasks.

## Primary Rule

Use CENTRAL DB as the canonical task system. Only touch legacy `autonomy` task queues when compatibility or migration work requires it.

## Key Responsibilities

- Create tasks with explicit scope, deliverables, and validation notes
- Encode dependency edges up front
- Promote only runnable work
- Reconcile any compatibility or mirror surfaces after CENTRAL is updated

## Main Commands

```bash
python3 scripts/central_task_db.py --help
python3 scripts/create_planner_task.py --help
autonomy task create --help
autonomy task update --help
autonomy task set-dependencies --help
autonomy task eligible --json
autonomy task blocked --json
```

## Workflow

1. Inspect or update the canonical task in CENTRAL DB first.
2. If a legacy autonomy queue still needs a task body, derive it from CENTRAL state rather than inventing a second source of truth.
3. Create or update dependency edges before promotion.
4. Promote only tasks that are actually runnable.
5. Reconcile execution outcomes back into CENTRAL first, then refresh any derived surfaces.

## Notes

- For numbering, prefer the CENTRAL task ID helpers instead of repeated existence probes.
- If implementation already landed before task creation, use the backfill flow so the task starts in an audit-ready state.
- Use `docs/autonomy_skills/autonomy-triage.md` only as supporting reference; this skill is about planning, not run-time diagnosis.
