# CENTRAL Task DB CLI

This document records the implemented CENTRAL DB-native control-plane commands.

## Canonical Rule

- the CENTRAL SQLite DB is the source of truth
- markdown task files and summary boards are bootstrap, import, export, or audit surfaces only
- planner, operator, runtime, and migration workflows should use `scripts/central_task_db.py`, not manual SQL or hand-edited canonical markdown

## Command Root

Use:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py <command>
```

DB path resolution order:

1. `--db-path`
2. `CENTRAL_TASK_DB_PATH`
3. `/home/cobra/CENTRAL/state/central_tasks.db`

Durability directory resolution:

1. `--durability-dir`
2. `/home/cobra/CENTRAL/durability/central_db`

Initialize first if needed:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init
```

## Durability Commands

These commands make the canonical DB portable and recoverable without changing the DB-first architecture.

### Publish a durable snapshot

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-create \
  --note "planner handoff after CENTRAL-OPS-26"
```

Behavior:

- captures a point-in-time SQLite backup from the live DB
- writes an immutable snapshot under `durability/central_db/snapshots/<snapshot_id>/`
- writes `manifest.json` with task/version inventory and planner/runtime digests
- updates `durability/central_db/latest.json` to point at the newest published snapshot

### List published snapshots

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-list
```

This gives operators a quick audit view of published recovery points.

### Restore a snapshot

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore

python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore \
  --snapshot-id 20260310T000000Z-abcdef12 \
  --db-path /tmp/central_tasks_restored.db
```

Behavior:

- restores the latest snapshot by default, or a named snapshot with `--snapshot-id`
- writes a pre-restore backup of the target DB unless `--no-backup-existing` is passed
- supports clean-checkout or alternate-path restores with `--db-path`

Recommended operator flow:

1. `snapshot-restore` after pulling the latest repo state
2. make planner updates through the DB CLI
3. `snapshot-create` before commit/push so the canonical DB state is durable and shareable

## Planner Commands

### Create or update repos

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-upsert \
  --repo-id CENTRAL \
  --repo-root /home/cobra/CENTRAL \
  --display-name CENTRAL \
  --alias central
```

Registry helpers:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-list
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-list --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-show --repo CENTRAL --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-resolve --repo moto-helper --json
```

`repo-list` is the canonical fast path for operators and planners that need the current tracked repo registry. It returns `repo_id`, `display_name`, `repo_root`, and active state; with `--json`, it also includes metadata, creation/update timestamps, aliases, and lookup context for debugging.

`repo-show` returns the full canonical record for one repo reference, resolving aliases/display names/root variants via the same lookup rules as `repo-resolve`.

Lookup rules:

- canonical `repo_id` remains the only stored task target identity
- planner-facing `--repo-id` filters and task payload `target_repo_id` fields accept canonical IDs, explicit aliases, display names, and repo-root basename variants
- lookup first prefers exact matches, then normalized matches that ignore case plus separator differences such as spaces, `_`, and `-`
- if multiple repos match the same normalized reference, the command fails explicitly instead of guessing

Preferred naming pattern:

- keep `repo_id` stable and canonical
- add `--alias` entries for common human-facing variants or legacy names
- use `repo-show` to inspect one repo's canonical identity for operator or planner debugging
- use `repo-resolve` when you are unsure which canonical `repo_id` a variant maps to

### Create a task

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-create --input /path/to/task.json --json
```

Expected JSON shape:

```json
{
  "task_id": "CENTRAL-OPS-20",
  "title": "Example task",
  "summary": "Short summary",
  "objective_md": "Objective text",
  "context_md": "Context text",
  "scope_md": "Scope text",
  "deliverables_md": "Deliverables text",
  "acceptance_md": "Acceptance text",
  "testing_md": "Testing text",
  "dispatch_md": "Dispatch text",
  "closeout_md": "Closeout text",
  "reconciliation_md": "Reconciliation text",
  "planner_status": "todo",
  "priority": 20,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "/home/cobra/CENTRAL",
  "approval_required": false,
  "metadata": {},
  "execution": {
    "task_kind": "mutating",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "additional_writable_dirs": [],
    "timeout_seconds": 1800,
    "metadata": {}
  },
  "dependencies": ["CENTRAL-OPS-14"]
}
```

### Scaffold a task draft from planner defaults

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py planner-new \
  --title "Fix planner-new scaffold" \
  --repo CENTRAL \
  --task-type implementation \
  --json > /tmp/task.json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-create --input /tmp/task.json
```

`planner-new`:

- allocates the next `task_id` in the selected series without manual ID probing
- fills required fields with sensible planner defaults
- writes a schema-valid draft JSON payload
- supports direct piping (`--json`) into `task-create --input -`

### Update a task with optimistic concurrency

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-update \
  --task-id CENTRAL-OPS-20 \
  --expected-version 1 \
  --input /path/to/patch.json
```

Notes:

- planner updates require `--expected-version`
- updates fail on version mismatch rather than silently clobbering another planner write
- planner updates reject active worker leases unless `--allow-active-lease` is passed for an explicit override workflow

Useful patch fields:

- `planner_status`
- `priority`
- `planner_owner`
- `worker_owner`
- `dependencies`
- `execution`
- any canonical task body field

### Reconcile closeout

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-reconcile \
  --task-id CENTRAL-OPS-20 \
  --expected-version 2 \
  --outcome done \
  --summary "Accepted after review" \
  --tests "manual review only" \
  --artifact /tmp/review-note.md
```

This updates planner-owned lifecycle state and records planner closeout metadata without requiring raw SQL.

### Inspect planner state

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-show --task-id CENTRAL-OPS-20 --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-list --planner-status todo
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-list --repo-id moto helper
```

### Ask for the next task ID in a series

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-id-next --series CENTRAL-OPS
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-id-next --series AUT-OPS --json
```

Behavior:

- uses a monotonic high-water mark for the series instead of backfilling historical gaps
- includes active reservations in the calculation, so planners do not need repeated `task-show` existence checks
- defaults to `CENTRAL-OPS` if `--series` is omitted

### Reserve a short contiguous task-ID range

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-id-reserve \
  --series CENTRAL-OPS \
  --count 3 \
  --reserved-for "dispatcher worker adoption series" \
  --note "laying out a tightly related task family"
```

Behavior:

- reserves the next contiguous range after the current task/reservation high-water mark
- enforces a small-range cap of 10 IDs per reservation
- defaults reservations to a 48-hour expiration window unless `--hours` is provided
- records reservation metadata and audit events in the canonical DB

### Inspect reservation visibility and reconciliation state

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-id-reservations
python3 /home/cobra/CENTRAL/scripts/central_task_db.py task-id-reservations --all --include-events --json
```

Semantics:

- active reservations stay visible until they either expire or every reserved ID has been created as a task
- `task-id-next`, `task-id-reserve`, and `task-id-reservations` reconcile expired/completed reservations before returning results
- completed reservations remain in history for audit, while expired reservations release unused IDs back to the series

## Operator Views

Implemented DB-generated read models:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-summary
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-eligible
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-blocked
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-repo --repo-id central
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-assignments
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-review
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-task-card --task-id CENTRAL-OPS-20
```

Rules:

- these surfaces read from DB state only
- JSON output is available with `--json`
- terminal output includes a generated/non-canonical banner
- `view-summary` and `view-review` surface planner/runtime mismatches so terminal `done` drift is visible instead of silent

## Repo Health

Repo health aggregation for the initial dispatcher and app adapters lives outside the DB CLI:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --json
```

This command aggregates:

- live CENTRAL dispatcher/runtime, test, queue, and smoke evidence
- repo-local adapter output from `/home/cobra/aimSoloAnalysis/tools/repo_health_adapter.py`, normalized into the canonical repo-health contract
- repo-local adapter output from `/home/cobra/motoHelper/tools/repo_health_adapter.py`, normalized into the canonical repo-health contract

The operator view reports `working_status`, `evidence_quality`, and explicit coverage semantics per repo. Contract and onboarding details are documented in `/home/cobra/CENTRAL/docs/repo_health.md`.

## Markdown Exports

Optional exports remain derived outputs only:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-summary-md
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-task-card-md --task-id CENTRAL-OPS-20
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-tasks-board-md
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-markdown-bundle
python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-repo-md --repo-id CENTRAL
```

Default output locations:

- `/home/cobra/CENTRAL/generated/portfolio_summary.md`
- `/home/cobra/CENTRAL/generated/task_cards/<task_id>.md`
- `/home/cobra/CENTRAL/generated/tasks.md`

Every generated markdown artifact is marked as generated from the CENTRAL DB and non-canonical.

`export-tasks-board-md` is the generated landing-page export for operators who still want a `tasks.md`-style board view without manual maintenance.

`export-markdown-bundle` writes the standard `generated/` markdown set in one shot:

- `generated/tasks.md`
- `generated/portfolio_summary.md`
- `generated/blocked_tasks.md`
- `generated/review_queue.md`
- `generated/assignments.md`
- `generated/per_repo/<repo_id>.md`
- `generated/task_cards/<task_id>.md`

`export-repo-md --repo-id <repo_id>` writes one repo-specific markdown queue view to `generated/per_repo/<repo_id>.md`.

## Runtime Commands

These commands implement the DB-native dispatcher/runtime control path.
For normal operator use, prefer the wrapper commands:

```bash
dispatcher start --max-workers 3
dispatcher config --max-workers 3
dispatcher config --codex-model gpt-5-codex
dispatcher status
dispatcher workers
```

Launcher rules:

- `dispatcher start --max-workers <n>` applies an immediate worker limit
- `dispatcher start --codex-model <model>` applies an immediate dispatcher-wide default Codex model
- `dispatcher config --max-workers <n>` persists the default launcher limit
- `dispatcher config --codex-model <model>` persists the default launcher Codex model
- worker model precedence is: task `execution.metadata.codex_model`, then dispatcher default, then the built-in fallback `gpt-5-codex`
- `dispatcher status` shows the active daemon limit plus the next-start default/source
- `dispatcher workers --json` is the canonical worker inspection surface for operators and future skills, including active-run Codex model metadata
- `dispatcher stop` and `dispatcher restart` perform a fast handoff: active workers keep running, lease metadata preserves adoption state, and the next dispatcher adopts them on startup
- graceful handoff extends active leases for a short restart window; if no dispatcher returns before that grace expires, stale-lease recovery can reclaim the task
- `CENTRAL_DISPATCHER_MAX_WORKERS=<n>` overrides launcher defaults for the current shell session
- `CENTRAL_DISPATCHER_CODEX_MODEL=<model>` overrides the saved default Codex model for the current shell session

### Inspect active and recent workers

Use the CENTRAL runtime worker inspector instead of scraping log files in routine cases:

```bash
dispatcher workers
dispatcher workers --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --json
python3 /home/cobra/CENTRAL/scripts/central_runtime.py worker-status --task-id CENTRAL-OPS-20 --json
```

The structured payload includes:

- active and recent task/run identity
- canonical runtime paths, including `runtime_paths.worker_results_dir`
- current runtime status and lease owner
- heartbeat freshness and lease expiry timing
- log file path, recency, size, and growth since the previous inspection
- result file metadata for the canonical `.worker-results` location
- concise heuristics for `healthy`, `low_activity`, `potentially_stuck`, `recently_finished`, or `recent_issue`

Routine guidance:

- start with `dispatcher workers`
- switch to `--json` when a skill or automation needs structured state
- use `runtime_paths.worker_results_dir` and each worker entry's `result.path` when you need the structured JSON output for a run
- tail raw logs only after the worker-status output identifies the task or run worth inspecting

Restart handoff guidance:

1. Prefer `dispatcher restart` over waiting for long-running workers to drain.
2. After restart, verify the new daemon with `dispatcher status`.
3. Use `dispatcher workers` to confirm the active run was adopted and heartbeats resumed.

### Discover eligible runtime work

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-eligible
```

### Claim work atomically

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-claim \
  --worker-id worker-01 \
  --queue-name default \
  --lease-seconds 900
```

### Renew heartbeats

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-heartbeat \
  --task-id CENTRAL-OPS-20 \
  --worker-id worker-01
```

### Move runtime state forward

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-transition \
  --task-id CENTRAL-OPS-20 \
  --status running \
  --worker-id worker-01

python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-transition \
  --task-id CENTRAL-OPS-20 \
  --status pending_review \
  --worker-id worker-01 \
  --artifact /tmp/result.json
```

### Recover expired leases

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py runtime-recover-stale --limit 50
```

Runtime rules implemented here:

- one active lease row per task
- atomic double-claim protection via transactional claim plus primary-key lease row
- heartbeat renewal extends `lease_expires_at`
- stale recovery returns work to reclaimable `queued` runtime state and records an audit event
- runtime transitions stay in runtime-owned tables; `runtime_status=done` now auto-reconciles planner status to `done` when no review is required
- `pending_review` remains unreconciled until review is completed explicitly
- mismatched terminal planner/runtime combinations are surfaced in operator views and dispatcher logs

## Bootstrap Migration

Import current bootstrap markdown into the CENTRAL DB with:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py migrate-bootstrap --json
```

Behavior:

- imports `tasks/CENTRAL-OPS-*.md`
- supplements missing bootstrap files from `central_task_system_tasks.md`
- preserves stable `task_id` values
- records source provenance in task metadata, artifacts, and events
- is duplicate-safe by default because existing task IDs are skipped
- can refresh existing imported tasks with `--update-existing`

## Rollback Guidance

This CLI mutates the DB directly. Rollback is operational, not markdown-first:

- restore the SQLite DB from `snapshot-restore` if a broad mutation was incorrect
- use `task-update`, `task-reconcile`, or runtime commands for targeted corrections
- treat markdown bootstrap surfaces as import/export evidence, not the rollback source of truth
