# Dispatcher System README

## Purpose

This is the CENTRAL-native dispatcher runbook.
Canonical CENTRAL planning, runtime state, generated views, and bootstrap import live in the CENTRAL DB workflow documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md).

Current implementation lives in this repository today:

- repo: `/home/cobra/CENTRAL`
- runtime script: `/home/cobra/CENTRAL/scripts/central_runtime.py`
- launcher wrapper: `/home/cobra/CENTRAL/scripts/dispatcher_control.py`

Dispatch extraction target:

- keep this document as the owner of planning and repo-health aggregation responsibility.
- move runtime code + scripts into a dedicated `dispatcher` repo when ready.
- keep control-plane monitoring in CENTRAL using `CENTRAL_DISPATCHER_ROOT` and script overrides in `scripts/repo_health.py` and `dispatcher status` wrappers.

## Runtime Contract

Preferred daily entrypoints:

- `dispatcher ...` for start/stop/status/log control from any shell with `~/.zshrc` loaded
- `python3 /home/cobra/CENTRAL/scripts/central_runtime.py ...` for direct CENTRAL-native runtime control
- `autonomy ...` is now legacy compatibility tooling for older autonomy surfaces only

Bootstrap the runtime with:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init
```

Notes:

- `dispatcher` now starts the CENTRAL-native runtime, not `autonomy dispatch daemon`.
- CENTRAL runtime state lives under `/home/cobra/CENTRAL/state/central_runtime` by default.
- Set `CENTRAL_WORKER_MODE=stub` for isolated smoke runs or `CENTRAL_WORKER_MODE=codex` for real worker execution.
- `dispatcher start --max-workers <n>` or `python3 /home/cobra/CENTRAL/scripts/central_runtime.py daemon --max-workers <n>` sets dispatcher concurrency.
- `dispatcher config --max-workers <n>` persists the launcher default worker limit for later starts and restarts.
- `dispatcher config --codex-model <model>` persists the dispatcher-wide default Codex model.
- `CENTRAL_DISPATCHER_CODEX_MODEL=<model>` overrides the saved default model for the current shell session.
- Worker model precedence is: task `execution.metadata.codex_model`, then dispatcher default, then the built-in fallback `gpt-5-codex`.

## Manual CLI Flow

Operator status and queue checks:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py status --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-summary --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-eligible --json
```

Dispatch execution:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py run-once
python3 /home/cobra/CENTRAL/scripts/central_runtime.py daemon --max-workers 3 --default-codex-model gpt-5-codex
python3 /home/cobra/CENTRAL/scripts/central_runtime.py stop
```

Worker inspection and review queues:

```bash
dispatcher workers
dispatcher workers --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py tail
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-review --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-assignments --json
```

Preferred routine inspection path:

- use `dispatcher workers` for the operator summary
- use `dispatcher workers --json` or `python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --json` for tool/skill integrations
- treat `/home/cobra/CENTRAL/state/central_runtime/.worker-results` as the canonical structured worker-output directory
- treat manual log tailing as a follow-up step only when the structured worker status points to a suspect run

Runtime smoke and self-check:

```bash
CENTRAL_WORKER_MODE=stub python3 /home/cobra/CENTRAL/scripts/central_runtime.py self-check
```

Legacy autonomy dispatcher path is no longer primary. If older autonomy state still needs inspection:

```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
autonomy dispatch status --profile default
```

Deprecated CENTRAL bridge flow:

```bash
autonomy central sync --central-root /home/cobra/CENTRAL --profile default
```

Use this only for transitional bootstrap import into the legacy autonomy DB.
It is no longer the primary workflow.

## Shell Entry Point

The preferred shell command is `dispatcher`, provided by `~/.zshrc`.

It is backed by:

- script: `/home/cobra/CENTRAL/scripts/dispatcher_control.py`

Supported commands:

```bash
dispatcher
dispatcher start
dispatcher start --max-workers 3
dispatcher start --codex-model gpt-5-codex
dispatcher restart
dispatcher restart --max-workers 3
dispatcher restart --codex-model gpt-5-codex
dispatcher stop
dispatcher status
dispatcher menu
dispatcher workers
dispatcher kill-task CENTRAL-OPS-20 --reason "operator stopped stuck worker"
dispatcher config
dispatcher config --max-workers 3
dispatcher config --codex-model gpt-5-codex
dispatcher logs
dispatcher follow
dispatcher once
```

Behavior:

- `dispatcher` defaults to `start`
- `dispatcher menu` opens an interactive numbered operator menu for start/stop/restart, config updates, status, workers, logs, checks, and kill-task
- auto-runs CENTRAL DB init if needed
- launches CENTRAL-native `daemon` in the background
- `dispatcher start --max-workers <n>` launches the daemon with `<n>` workers
- `dispatcher start --codex-model <model>` applies an immediate dispatcher-wide default Codex model
- `dispatcher workers` reports active and recent worker runs with heartbeat freshness, log recency, and stuck-suspect heuristics
- `dispatcher workers --json` exposes `runtime_paths.worker_results_dir` plus per-run `result.path` metadata plus active-run Codex model metadata for the canonical structured output surface
- `dispatcher kill-task <task-id>` is the operator stop path: it fails planner/runtime state in CENTRAL DB, terminates the worker if it is still running, and prevents immediate retry
- `dispatcher config --max-workers <n>` saves the default worker limit to `/home/cobra/CENTRAL/state/central_runtime/dispatcher-config.json`
- `dispatcher config --codex-model <model>` saves the default Codex model to `/home/cobra/CENTRAL/state/central_runtime/dispatcher-config.json`
- `dispatcher restart` preserves the currently running worker limit unless a new `--max-workers` value, environment override, or saved config replaces it
- `dispatcher restart` preserves the currently running default Codex model unless a new `--codex-model` value, environment override, or saved config replaces it
- `dispatcher stop` and `dispatcher restart` are restart-safe handoff operations: the daemon exits promptly, active workers keep running, and the next dispatcher instance adopts them from persisted lease metadata
- active worker supervision metadata is persisted in `task_active_leases.lease_metadata_json`, including run id, worker pid, process identity, prompt/log/result paths, and the selected Codex model needed for adoption and audit
- a graceful stop extends active leases for a short handoff window; if no dispatcher returns before that grace expires, normal stale-lease recovery rules still apply
- `CENTRAL_DISPATCHER_MAX_WORKERS=<n>` overrides the saved/default worker limit for the current shell session
- `CENTRAL_DISPATCHER_CODEX_MODEL=<model>` overrides the saved/default Codex model for the current shell session
- writes launcher output to CENTRAL runtime state
- uses the CENTRAL runtime lock file as the source of truth for running state
- `dispatcher status` shows both the active daemon worker limit/model and the next-start launcher defaults/sources

Optional PATH launcher for the interactive menu:

```bash
ln -sf /home/cobra/CENTRAL/scripts/dispatcher_menu.py ~/bin/dispatcher-menu
dispatcher-menu
```

### Extracted Dispatcher Boundary

- CENTRAL owns portfolio planning, task truth, repo index, and health questioning (`is the dispatcher working?`).
- runtime execution and `dispatcher` process operations stay in the extracted runtime repo as its peer service.
- CENTRAL should only treat runtime as an observed peer; it should not assume dispatcher internals are in-tree.
- run repo-health snapshot for `dispatcher` with explicit boundaries, for example:

```bash
CENTRAL_DISPATCHER_ROOT=/path/to/dispatcher-repo python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo dispatcher
```

Restart-safe operator path:

1. Run `dispatcher restart` for routine code/config restarts while long workers are active.
2. Confirm the new daemon is up with `dispatcher status`.
3. Confirm adoption with `dispatcher workers` or `dispatcher workers --json`.
4. If the daemon was stopped intentionally, restart it before the handoff grace expires so active leases do not age into stale recovery.

## On-Disk State

Default CENTRAL runtime paths:

- DB: `/home/cobra/CENTRAL/state/central_tasks.db`
- runtime state root: `/home/cobra/CENTRAL/state/central_runtime`
- dispatcher lock: `/home/cobra/CENTRAL/state/central_runtime/dispatcher.lock`
- dispatcher log: `/home/cobra/CENTRAL/state/central_runtime/dispatcher.log`
- launcher log: `/home/cobra/CENTRAL/state/central_runtime/dispatcher-launcher.log`
- launcher config: `/home/cobra/CENTRAL/state/central_runtime/dispatcher-config.json`
- worker prompts: `/home/cobra/CENTRAL/state/central_runtime/.worker-prompts`
- worker results: `/home/cobra/CENTRAL/state/central_runtime/.worker-results` (canonical structured worker output)
- worker logs: `/home/cobra/CENTRAL/state/central_runtime/.worker-logs`
- worker supervision metadata: persisted in CENTRAL DB lease rows, not only in daemon memory

## Codex Skills In Use

These are the relevant skills for dispatch-system support:

- `autonomy-operator`
  - run dispatcher, inspect workers, inspect queue pressure, tail logs
- `autonomy-planner`
  - create tasks, update tasks, inspect eligible/blocked graphs
- `autonomy-triage`
  - inspect failures, retries, stale reviews, approve/reject paths
- `multi-repo-planner`
  - keep canonical CENTRAL DB tasks, repo targeting, and cross-repo priorities aligned

## Canonical Docs

Canonical autonomy operator/planner/triage docs now live in `CENTRAL`:

- [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
- [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
- [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
- [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)

`/home/cobra/photo_auto_tagging/docs/autonomy_skills/` is now implementation-local and should only keep stubs or code-adjacent notes.

## Canonical CENTRAL DB Workflow

The planner, not the user, owns maintaining canonical CENTRAL task state in the CENTRAL DB.

Working sequence:

1. Create or update the canonical task in CENTRAL DB with [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py).
2. Query generated views from CENTRAL DB for summary, eligibility, blocked work, assignments, review, and task detail.
3. Use CENTRAL DB runtime commands for claim, heartbeat, transition, and stale-lease recovery.
4. Use bootstrap markdown only for import, export, or archival needs.
5. After worker completion or review outcome, reconcile planner state in CENTRAL DB first, then refresh generated summaries or exports.

Canonical commands:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-create --input /path/to/task.json --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-update --task-id CENTRAL-OPS-20 --expected-version 1 --input /path/to/patch.json --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-summary --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-eligible --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-claim --worker-id worker-01 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py migrate-bootstrap --json
```

Ownership rules:

- Planner owns DB-native task creation, prompt refinement, dependency maintenance, and reconciliation.
- Worker owns implementation plus closeout evidence: tests run, commit/ref, and blocker statement if blocked.
- Planner owns updates to CENTRAL DB first, then generated summaries or repo-local mirrors only when still useful.
- User should only need to request work or ask for status; the planner performs the bookkeeping.

Source roles during the transition:

- CENTRAL SQLite DB: canonical planner truth, dependencies, ownership, runtime state, and reconciliation
- [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py): canonical planner, operator, runtime, and migration command surface
- markdown task files and `tasks.md`: bootstrap import, generated export, or archival surfaces only
- repo-local markdown boards: optional mirrors, local intake, or repo-specific roadmap context

## Planner Rule

The user should rarely have to create or update dispatch tasks manually.

Planner responsibility:

- add and update support tasks
- keep canonical CENTRAL DB task records current
- convert planning intent into dispatchable tasks
- decide when a task belongs in CENTRAL canonical tracking vs the autonomy DB

User responsibility:

- mostly just start the dispatcher and ask for status

## Review And Retry Runbook

Check these surfaces each dispatch cycle or at least daily:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py status --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-review --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-assignments --json
```

Per-task inspection:

```bash
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --task-id CENTRAL-OPS-20 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-show --task-id CENTRAL-OPS-20 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-task-card --task-id CENTRAL-OPS-20 --json
dispatcher kill-task CENTRAL-OPS-20 --reason "operator stopped stuck worker"
python3 /home/cobra/CENTRAL/scripts/central_runtime.py tail
```

Decision rules:

- Use `dispatcher kill-task <task-id>` when the operator wants execution to stop now and the task must not immediately retry.
- Reconcile `done` or `blocked` with `python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-reconcile ...` when planner review is complete.
- Retry by returning runtime state to a claimable path through CENTRAL-native runtime and planner judgment.
- Leave blocked when upstream dependencies or missing external inputs still prevent useful progress.

Required closeout evidence:

- commands/tests run and result summary
- commit hash, branch, or file reference
- concise blocker statement when not done

Stale-review clearing rhythm:

1. Inspect `review-aging` output.
2. Open the task/run evidence.
3. Approve, reject, or reset in the same session.
4. Mirror the decision back to central tracking if it changes portfolio state.

## Source-Of-Truth Migration

Phase 0: canonical authoring bootstrap

- Planner-owned tasks are authored in `CENTRAL/tasks/`.
- autonomy DB is authoritative for runtime execution state after a canonical task is ingested there.
- Repo boards may still provide intake and mirror context where migration is incomplete.

Phase 1: planner-owned execution

- New dispatchable planner-owned work starts from a canonical CENTRAL task file.
- autonomy DB mirrors that task into runnable state and review workflow.
- Repo boards remain summarized mirrors for humans and repo-specific notes.

Phase 2: CENTRAL-authored, autonomy-executed steady state

- `CENTRAL/tasks/` remains the authored source of truth for planner-owned work.
- autonomy DB remains the execution-state system of record.
- Repo boards keep only local roadmap notes, optional mirrors, or archived snapshots.

Drift resolution:

- If autonomy and markdown disagree, keep authored task content in `CENTRAL/tasks/` and runtime state in autonomy aligned according to the current phase.
- Planner fixes the non-authoritative surface in the same work session that discovers drift.
- Do not resolve drift by editing SQLite directly.

Rollback:

- Stop creating new DB-only tasks.
- Continue authoring planner-owned tasks in `CENTRAL/tasks/`.
- Export active autonomy state back into CENTRAL canonical tasks and summary records until tooling gaps are addressed.
