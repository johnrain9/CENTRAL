# AI GUIDE

This guide is for AI workers operating inside `CENTRAL`.

Use it to orient quickly, reduce exploratory search, and avoid re-learning the repo from scratch on every task.

Read this before doing broad repo inspection.

## What CENTRAL Is

CENTRAL is the planner-owned control plane for a multi-repo portfolio.

It owns:

- the canonical SQLite task database
- planner task creation/update/reconciliation
- dispatcher/runtime orchestration
- worker leases, events, artifacts, and runtime status
- generated planner/operator views
- some cross-repo operator tooling

It does **not** own most product code in the portfolio. It owns planning, dispatch, visibility, and coordination.

## Read These First

Start in this order:

1. [README.md](/Users/paul/projects/CENTRAL/README.md)
2. [docs/planner_coordinator_bootstrap.md](/Users/paul/projects/CENTRAL/docs/planner_coordinator_bootstrap.md)
3. Then open only the specific file(s) relevant to your task.

If your task is about:

- task DB behavior: [scripts/central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)
- dispatcher/runtime behavior: [scripts/central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py)
- operator wrapper commands: [scripts/dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)
- task creation ergonomics: [scripts/create_planner_task.py](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py)
- task schema/design rules: [docs/central_task_db_schema.md](/Users/paul/projects/CENTRAL/docs/central_task_db_schema.md)
- task lifecycle / concurrency: [docs/central_task_concurrency.md](/Users/paul/projects/CENTRAL/docs/central_task_concurrency.md)
- operator workflow: [docs/central_task_cli.md](/Users/paul/projects/CENTRAL/docs/central_task_cli.md)
- capability memory design: [docs/capability_memory_hld.md](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md) and [docs/capability_memory_lld_01_schema.md](/Users/paul/projects/CENTRAL/docs/capability_memory_lld_01_schema.md)

## High-Signal Repo Map

Top-level areas that matter most:

- [scripts](/Users/paul/projects/CENTRAL/scripts)
  - primary operational surface
  - most important files:
    - [central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)
    - [central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py)
    - [dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)
    - [create_planner_task.py](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py)
    - [review_doc.py](/Users/paul/projects/CENTRAL/scripts/review_doc.py)

- [docs](/Users/paul/projects/CENTRAL/docs)
  - design docs, operator docs, lifecycle docs

- [db/migrations](/Users/paul/projects/CENTRAL/db/migrations)
  - canonical DB schema migrations

- [tests](/Users/paul/projects/CENTRAL/tests)
  - most runtime/dispatcher regressions live here

- [state](/Users/paul/projects/CENTRAL/state)
  - live DB and runtime artifacts
  - not a source file area, but often the right place to inspect runtime truth

- [generated](/Users/paul/projects/CENTRAL/generated)
  - derived views only, not canonical truth

- [tasks](/Users/paul/projects/CENTRAL/tasks)
  - historical/bootstrap markdown tasks, not the steady-state write surface

## Canonical Rules

These rules are more important than convenience:

- the CENTRAL SQLite DB is the source of truth
- generated markdown is derived output only
- bootstrap markdown is historical/import material, not canonical planner state
- task lifecycle semantics live in CENTRAL, not in repo-local mirrors
- dispatcher is the data plane; CENTRAL is the control plane

Live DB path:

- [state/central_tasks.db](/Users/paul/projects/CENTRAL/state/central_tasks.db)

## Where To Look For Common Tasks

If the task is about planner status, reconciliation, eligibility, dependencies, audits, or task cards:

- start in [scripts/central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py)

If the task is about worker spawning, runtime transitions, heartbeats, adoption, stale recovery, worker logs/results, or dispatcher status:

- start in [scripts/central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py)

If the task is about shell UX like `dispatcher start/status/logs/follow/kill-task`:

- start in [scripts/dispatcher_control.py](/Users/paul/projects/CENTRAL/scripts/dispatcher_control.py)

If the task is about task creation ergonomics or required task fields:

- start in [scripts/create_planner_task.py](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py)

If the task is about document review tooling:

- start in [scripts/review_doc.py](/Users/paul/projects/CENTRAL/scripts/review_doc.py)

If the task is about repo health aggregation:

- start in [scripts/repo_health.py](/Users/paul/projects/CENTRAL/scripts/repo_health.py)

## How To Work Efficiently Here

Do not start with a broad repo crawl unless the task is truly ambiguous.

Preferred pattern:

1. Read [README.md](/Users/paul/projects/CENTRAL/README.md).
2. Read the one or two primary files for the task area.
3. Read the one relevant test file if behavior is unclear.
4. Only then expand outward.

Use existing artifacts before rediscovering context:

- dispatcher log: [state/central_runtime/dispatcher.log](/Users/paul/projects/CENTRAL/state/central_runtime/dispatcher.log)
- worker logs: [state/central_runtime/.worker-logs](/Users/paul/projects/CENTRAL/state/central_runtime/.worker-logs)
- worker results: [state/central_runtime/.worker-results](/Users/paul/projects/CENTRAL/state/central_runtime/.worker-results)
- worker prompts: [state/central_runtime/.worker-prompts](/Users/paul/projects/CENTRAL/state/central_runtime/.worker-prompts)

If a task is tied to an existing runtime failure, inspect the specific task card first:

```bash
python3 scripts/central_task_db.py task-show --task-id <TASK_ID> --json
```

That is usually more efficient than starting in raw logs.

## Planner Status UI

A web-based live control surface for queue state, active workers, audits, and repo breakdown.

```bash
python3 scripts/planner_ui.py          # serves at http://localhost:7099
python3 scripts/planner_ui.py --port 7099 --host 127.0.0.1
```

- Single-file Flask server: `scripts/planner_ui.py`
- API endpoint: `GET /api/data` — aggregates all CENTRAL views into one payload
- Task detail: `GET /api/task/<task_id>` — full task card
- Validation test: `python3 tests/test_planner_ui.py`
- Read-only in v1. No dispatcher mutation controls.

## Common Commands

Run from:

```bash
cd /Users/paul/projects/CENTRAL
```

High-signal control-plane commands:

```bash
python3 scripts/central_task_db.py status --json
python3 scripts/central_task_db.py view-summary --json
python3 scripts/central_task_db.py view-eligible --json
python3 scripts/central_task_db.py view-review --json
python3 scripts/central_task_db.py task-list --json
python3 scripts/central_task_db.py task-show --task-id <TASK_ID> --json
python3 scripts/central_task_db.py runtime-requeue-task --task-id <TASK_ID> --reason "..." --reset-retry-count --json
python3 scripts/central_task_db.py operator-fail-task --task-id <TASK_ID> --reason "..." --json
```

High-signal runtime/operator commands:

```bash
dispatcher status
dispatcher workers
dispatcher logs
dispatcher follow
dispatcher start --max-workers 3
dispatcher restart --max-workers 3
dispatcher stop
```

Task creation:

```bash
python3 scripts/create_planner_task.py --help
```

## Audit Model

CENTRAL uses paired audit tasks for most non-trivial implementation work.

Core behavior:

- implementation tasks usually auto-create a paired audit task
- the audit task depends on the implementation task
- implementation success is not enough by itself
- implementation usually moves to `awaiting_audit`
- successful audit closes both the audit task and the parent implementation task
- failed audit should not silently close the parent

When investigating lifecycle behavior, check both:

- planner status
- runtime status

And check linked audit metadata on the task card.

## Common Traps

1. Treating markdown as canonical.
- Do not trust `tasks.md` or `generated/` over the DB.

2. Looking at the wrong layer.
- Planner logic is mostly in [central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py).
- Runtime orchestration is mostly in [central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py).

3. Confusing planner status with runtime status.
- They are related but not the same.

4. Assuming a failed runtime task means bad task logic.
- Sometimes the failure is provider/runtime infrastructure, not the task itself.
- Inspect worker logs/results before concluding the task is wrong.

5. Making broad changes in dispatcher when the issue belongs in CENTRAL control-plane logic.

6. Rebuilding a tool or workflow that already exists.
- Check `scripts/`, `docs/`, and recent tasks first.

## When To Update This Guide

Update this file when you discover durable knowledge that would help the next worker:

- a better “start here” path for a subsystem
- a recurring trap
- a key operator command that is easy to miss
- a structural repo fact that workers repeatedly rediscover
- a workflow change that should alter how future workers navigate CENTRAL

Do not update it for transient one-off debugging notes.

## Definition Of A Good CENTRAL Task

A good CENTRAL task for an AI worker should include:

- a narrow objective
- clear acceptance
- explicit non-goals where useful
- the likely starting file(s) or doc(s)
- one primary validation path

If a task is broad, split it before dispatch when practical.

## If You Are Unsure

Start with:

1. [README.md](/Users/paul/projects/CENTRAL/README.md)
2. [scripts/central_task_db.py](/Users/paul/projects/CENTRAL/scripts/central_task_db.py) or [scripts/central_runtime.py](/Users/paul/projects/CENTRAL/scripts/central_runtime.py), depending on whether the problem is planner-side or runtime-side
3. the relevant test file in [tests](/Users/paul/projects/CENTRAL/tests)

Then stop expanding once you have enough context to act.

## Changelog

- 2026-03-19 20:12 MDT: Created initial `AI_GUIDE.md` for CENTRAL with repo map, starting paths, common commands, audit model, and common traps.
- 2026-03-19 20:13 MDT: Standardized guide maintenance pattern to include a minimal timestamped changelog at the bottom.
- 2026-03-19 21:50 MDT: Added Planner Status UI section (`scripts/planner_ui.py`, `tests/test_planner_ui.py`). Flask-based, serves at port 7099, read-only v1.
