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

Initialize first if needed:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init
```

## Planner Commands

### Create or update repos

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-upsert \
  --repo-id CENTRAL \
  --repo-root /home/cobra/CENTRAL \
  --display-name CENTRAL
```

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
```

## Operator Views

Implemented DB-generated read models:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-summary
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-eligible
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-blocked
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-repo --repo-id CENTRAL
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-assignments
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-review
python3 /home/cobra/CENTRAL/scripts/central_task_db.py view-task-card --task-id CENTRAL-OPS-20
```

Rules:

- these surfaces read from DB state only
- JSON output is available with `--json`
- terminal output includes a generated/non-canonical banner

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
- runtime transitions stay in runtime-owned tables; planner lifecycle is reconciled separately

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

- restore the SQLite DB from backup if a broad mutation was incorrect
- use `task-update`, `task-reconcile`, or runtime commands for targeted corrections
- treat markdown bootstrap surfaces as import/export evidence, not the rollback source of truth
