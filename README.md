# CENTRAL

CENTRAL is the planner-owned control plane for a multi-repo portfolio. It is both:

- the canonical source of truth for planner-owned tasks, assignments, dependencies, and reconciliation
- the operator/runtime surface for dispatching and observing worker execution

This README is written for both humans and AI agents. If you read only this file, you should understand what CENTRAL owns, how the repository is laid out, which commands matter, and which deeper docs to open next.

## What CENTRAL Is

At a high level:

1. Planner truth lives in a SQLite database managed by CENTRAL.
2. The dispatcher/runtime also operates against that same CENTRAL DB.
3. Markdown task files and generated boards still exist, but they are bootstrap, import, export, or archival surfaces only.
4. CENTRAL also hosts operator docs, some cross-repo health tooling, and a few CENTRAL-owned utility tools.

If a markdown summary disagrees with the DB, the DB wins.

## How CENTRAL Relates To The Portfolio

CENTRAL is not just another app repo. Its role is different:

- product repositories own product code and repo-local implementation details
- CENTRAL owns planner-facing canonical task state across that portfolio
- CENTRAL targets work at specific repos through task metadata such as `target_repo_id` and `target_repo_root`
- repo-local task boards may still exist as intake or mirrors, but they are not the planner-owned source of truth once work has been promoted into CENTRAL
- repo-local health adapters can report into CENTRAL, but CENTRAL does not invent health data for them

## Primary Responsibilities

CENTRAL currently owns these concerns:

- canonical planner task storage in [`state/central_tasks.db`](/home/cobra/CENTRAL/state/central_tasks.db)
- DB migrations and the main task CLI in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
- CENTRAL-native dispatcher/runtime behavior in [`scripts/central_runtime.py`](/home/cobra/CENTRAL/scripts/central_runtime.py)
- operator wrapper commands in [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py)
- generated operator views under [`generated/`](/home/cobra/CENTRAL/generated)
- bootstrap task/task-packet markdown under [`tasks/`](/home/cobra/CENTRAL/tasks), [`tasks.md`](/home/cobra/CENTRAL/tasks.md), and [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md)
- canonical operator and skill-facing docs under [`docs/`](/home/cobra/CENTRAL/docs)
- repo health aggregation in [`scripts/repo_health.py`](/home/cobra/CENTRAL/scripts/repo_health.py)

CENTRAL does not own product code for the rest of the portfolio. It owns planning, dispatch, visibility, and a small amount of cross-repo ops tooling.

## Mental Model

Use this model when operating the repo:

- `tasks` table: the canonical planner-owned task record
- `task_execution_settings`: execution policy such as task kind, sandbox mode, approval mode, timeout
- `task_dependencies`: edges that determine eligibility
- runtime tables: claim/lease state, events, artifacts, and current worker status
- generated markdown: convenience output derived from DB state
- bootstrap markdown: historical scaffolding and import source, not steady-state truth

Steady-state flow:

1. Planner creates or updates a task in the CENTRAL DB.
2. Dispatcher queries eligible work from the DB.
3. Runtime claims a task, records lease state, and launches a worker.
4. Worker writes structured results and logs under `state/central_runtime`.
5. Planner/operator reviews output and reconciles the task in the DB.
6. Optional markdown exports are regenerated from DB state.

## Architecture

### Planner Layer

Planner-owned state is managed by [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py).

This layer owns:

- task definitions
- planner lifecycle status: `todo`, `in_progress`, `blocked`, `done`
- repo targeting
- dependency management
- assignments and reconciliation
- DB-native views and markdown exports
- durability snapshots for DB handoff/recovery

### Runtime Layer

Runtime behavior is managed by [`scripts/central_runtime.py`](/home/cobra/CENTRAL/scripts/central_runtime.py) and wrapped by [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py).

This layer owns:

- eligible-task discovery
- atomic claiming and leasing
- worker heartbeats
- runtime status transitions such as `claimed`, `running`, `pending_review`, `failed`, `timeout`, `done`
- stale-lease recovery
- worker adoption across dispatcher restarts
- worker log/result path tracking

### Durability Layer

The live DB is local, but CENTRAL supports publish/restore snapshots for handoff and recovery.

- live DB: [`state/central_tasks.db`](/home/cobra/CENTRAL/state/central_tasks.db)
- migrations: [`db/migrations/`](/home/cobra/CENTRAL/db/migrations)
- durability docs: [`docs/central_db_durability.md`](/home/cobra/CENTRAL/docs/central_db_durability.md)
- default durability root: `durability/central_db` when snapshots are created

### Generated Surface Layer

Generated outputs are convenience views, not a second source of truth.

Common generated artifacts:

- [`generated/tasks.md`](/home/cobra/CENTRAL/generated/tasks.md)
- [`generated/portfolio_summary.md`](/home/cobra/CENTRAL/generated/portfolio_summary.md)
- [`generated/blocked_tasks.md`](/home/cobra/CENTRAL/generated/blocked_tasks.md)
- [`generated/review_queue.md`](/home/cobra/CENTRAL/generated/review_queue.md)
- [`generated/assignments.md`](/home/cobra/CENTRAL/generated/assignments.md)
- [`generated/per_repo/`](/home/cobra/CENTRAL/generated/per_repo)
- [`generated/task_cards/`](/home/cobra/CENTRAL/generated/task_cards)

## Repository Layout

Top-level directories and files that matter most:

- [`scripts/`](/home/cobra/CENTRAL/scripts)
  - operational CLIs and wrappers
  - most important files: `central_task_db.py`, `central_runtime.py`, `dispatcher_control.py`, `repo_health.py`
- [`docs/`](/home/cobra/CENTRAL/docs)
  - canonical design and runbook docs
  - start here if you need schema details or exact operating rules
- [`db/migrations/`](/home/cobra/CENTRAL/db/migrations)
  - SQLite schema migrations for the canonical DB
- [`tasks/`](/home/cobra/CENTRAL/tasks)
  - bootstrap task records and archival task files
- [`generated/`](/home/cobra/CENTRAL/generated)
  - derived markdown exports from DB state
- [`state/`](/home/cobra/CENTRAL/state)
  - local runtime state and the live SQLite DB
  - ignored by git via [`.gitignore`](/home/cobra/CENTRAL/.gitignore)
- [`tests/`](/home/cobra/CENTRAL/tests)
  - smoke and regression tests for durability, runtime handoff, repo health, and related tooling
- [`tools/`](/home/cobra/CENTRAL/tools)
  - supporting tools currently including `repo_health`, `voice_ptt`, and `voice_ptt_v2`
- [`skills/`](/home/cobra/CENTRAL/skills)
  - repo-local skill content; external Codex skill docs also point back into CENTRAL docs
- [`.ops/`](/home/cobra/CENTRAL/.ops)
  - operational wiring such as launcher/systemd/autostart helpers for workstation tooling

Important top-level markdown surfaces:

- [`tasks.md`](/home/cobra/CENTRAL/tasks.md): legacy/bootstrap board plus summary surface
- [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md): bootstrap packet/history for the CENTRAL task-system workstream
- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md): dispatcher-specific runbook

## Source Of Truth Rules

These rules matter more than any individual command:

1. The CENTRAL SQLite DB is canonical.
2. The live working DB is local and git-ignored.
3. Markdown task files are not the steady-state planner write surface.
4. Generated markdown is derived output only.
5. If you need to recover or hand off DB state, use snapshot commands instead of treating markdown as rollback truth.

For exact schema/design references:

- [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- [`docs/central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
- [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
- [`docs/central_task_db_bootstrap.md`](/home/cobra/CENTRAL/docs/central_task_db_bootstrap.md)

## Core Command Surfaces

### DB Control Plane

Primary entrypoint:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py <command>
```

High-signal commands:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init
python3 /home/cobra/CENTRAL/scripts/central_task_db.py status --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-list --planner-status todo
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-show --task-id CENTRAL-OPS-20 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-summary --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-eligible --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-review --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-assignments --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-eligible --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-recover-stale --limit 50 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-markdown-bundle
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-list --json
```

DB path resolution order:

1. `--db-path`
2. `CENTRAL_TASK_DB_PATH`
3. `/home/cobra/CENTRAL/state/central_tasks.db`

### Dispatcher And Runtime

Most operators use the wrapper:

```bash
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py <command>
```

Common commands:

```bash
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py start
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py start --max-workers 3
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py restart
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py status
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py workers --json
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py logs
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py stop
```

Direct runtime entrypoint:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py <command>
```

Common runtime commands:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py status --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py run-once
python3 /home/cobra/CENTRAL/scripts/central_runtime.py daemon --max-workers 3
python3 /home/cobra/CENTRAL/scripts/central_runtime.py self-check
python3 /home/cobra/CENTRAL/scripts/central_runtime.py stop
```

Useful runtime environment variables:

- `CENTRAL_TASK_DB_PATH`
- `CENTRAL_RUNTIME_STATE_DIR`
- `CENTRAL_WORKER_MODE`
- `CENTRAL_DISPATCHER_MAX_WORKERS`

Notes:

- a `dispatcher` shell helper may exist in interactive shells, but the repository-owned stable entrypoint is [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py)
- `dispatcher stop` and `dispatcher restart` are designed for handoff: active workers can keep running and be adopted by the next daemon

### Repo Health

Repo health is separate from the task DB CLI:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --json
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo dispatcher --json
```

This aggregates CENTRAL dispatcher/runtime health plus registered repo-local adapters.

### Bootstrap Import And Exports

Import transitional markdown into the DB:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py migrate-bootstrap --json
```

Export non-canonical markdown from DB state:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-summary-md
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-task-card-md --task-id CENTRAL-OPS-20
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-tasks-board-md
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-repo-md --repo-id CENTRAL
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-markdown-bundle
```

### Durability And Recovery

Publish and restore DB snapshots with:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-create --note "planner handoff"
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-list --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore
```

This is the intended recovery/handoff path when local DB state must move between operators or machines.

## Typical Workflows

### If You Are Another AI Agent

Recommended sequence:

1. Read this README.
2. Inspect [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md) for the exact command contract.
3. Run `python3 /home/cobra/CENTRAL/scripts/central_task_db.py status --json`.
4. Run `python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py status`.
5. Use DB views instead of scraping markdown or log files first.
6. Treat `tasks/` and `tasks.md` as context/history unless the task explicitly says to update generated/bootstrap surfaces too.

Operationally:

- ask the DB what exists
- ask the runtime what is active
- inspect generated markdown only when a human-readable export is useful
- use deeper docs for detail, not for first-pass orientation

### Adding Or Updating Canonical Task State

1. Initialize or restore the DB if needed.
2. Create/update the task through [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py).
3. Inspect `view-summary`, `view-eligible`, or `task-show`.
4. Reconcile closeout in the DB first.
5. Regenerate markdown exports only if the workflow still needs them.

### Operating The Dispatcher

1. Confirm DB health with `status --json`.
2. Start or inspect the dispatcher via [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py).
3. Inspect workers with `workers --json` or `central_runtime.py worker-status --json`.
4. Use review/assignment views from the DB CLI.
5. Prefer restart-safe handoff behavior over killing long-running workers blindly.

### Recovering After Drift Or Loss

1. Use `snapshot-list` and `snapshot-restore` for DB recovery.
2. Use `runtime-recover-stale` for expired worker leases.
3. Do not treat markdown task cards as canonical recovery input.

## Current Operational Assumptions

These assumptions appear repeatedly across the repo:

- Python 3 is the main runtime; most core scripts are single-file stdlib-oriented CLIs
- the default working DB is local at `state/central_tasks.db`
- local runtime state is under `state/central_runtime`
- worker results are written under `state/central_runtime/.worker-results`
- worker logs are written under `state/central_runtime/.worker-logs`
- the dispatcher can run in `stub` mode for smoke tests or `codex` mode for real worker execution
- repo-local markdown boards in other repositories may still exist as intake or mirrors, but CENTRAL DB is the planner-owned truth

## Validation And Tests

Useful validation entrypoints in this repo:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py --help
python3 /home/cobra/CENTRAL/scripts/central_runtime.py --help
python3 /home/cobra/CENTRAL/scripts/dispatcher_control.py --help
python3 /home/cobra/CENTRAL/scripts/repo_health.py --help
bash /home/cobra/CENTRAL/tests/test_central_db_durability.sh
python3 -m unittest tests.test_dispatcher_restart_handoff
python3 -m unittest tests.test_repo_health
python3 -m unittest tests.test_repo_health_contract
python3 -m unittest tests.test_voice_ptt_v2
```

Validation strategy:

- use CLI `--help` and read-only status commands first
- use temp DB paths for DB-mutating smoke tests when possible
- avoid assuming `state/` contents are committed or shared unless a snapshot has been published

## Known Limitations And Transition Notes

- the live canonical DB is local and git-ignored, so a fresh checkout is not automatically in sync until you initialize or restore it
- some bootstrap and generated markdown still exists for migration/history and can drift from live DB state
- older `autonomy` workflows still appear in docs and neighboring repos, but the steady-state model here is CENTRAL-native DB plus runtime
- the `dispatcher` shell command is a convenience surface, not the repository-owned source of truth; use the Python scripts directly in automation
- repo health is intentionally adapter-based, so CENTRAL only knows what each repo exposes honestly
- `tools/voice_ptt` and `tools/voice_ptt_v2` are CENTRAL-owned utility workstreams, but they are adjacent to the core planner/dispatcher architecture rather than part of the canonical task control plane

## Deeper Docs

Open these next depending on what you need:

- task system direction: [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- DB schema: [`docs/central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
- DB bootstrap/init: [`docs/central_task_db_bootstrap.md`](/home/cobra/CENTRAL/docs/central_task_db_bootstrap.md)
- DB CLI/runbook: [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- planner/runtime integration model: [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
- dispatcher-specific runbook: [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- DB durability/handoff: [`docs/central_db_durability.md`](/home/cobra/CENTRAL/docs/central_db_durability.md)
- repo health aggregator: [`docs/repo_health.md`](/home/cobra/CENTRAL/docs/repo_health.md)
- autonomy skill docs hosted in CENTRAL: [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
- voice PTT workstreams: [`docs/voice_ptt.md`](/home/cobra/CENTRAL/docs/voice_ptt.md) and [`docs/voice_ptt_v2.md`](/home/cobra/CENTRAL/docs/voice_ptt_v2.md)

## Short Version

If you forget everything else, remember this:

- CENTRAL DB is the source of truth
- `scripts/central_task_db.py` is the main planner/operator CLI
- `scripts/central_runtime.py` and `scripts/dispatcher_control.py` run the execution plane
- `generated/` is derived output
- `tasks/` and `tasks.md` are no longer the steady-state canonical system
