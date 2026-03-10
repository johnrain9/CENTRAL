# CENTRAL SQLite Task Schema

This document defines the canonical SQLite schema for planner-owned task truth in `CENTRAL`.

## Source Of Truth

- Canonical planner task truth lives in a SQLite database managed by `CENTRAL`.
- Markdown is not canonical.
- Any markdown summary, export, or task card is generated from DB state or retained only as migration scaffolding.

## Design Goals

The schema must support:

- hundreds or thousands of tasks
- multiple planners updating task definitions safely
- multiple workers and dispatcher processes operating concurrently
- dependency-aware eligibility queries
- assignment, review, retry, and closeout history
- generated summary views without making those views canonical

## Core Tables

### `repos`

Purpose:

- normalize target repositories and execution roots

Columns:

- `repo_id` `TEXT PRIMARY KEY`
- `display_name` `TEXT NOT NULL`
- `repo_root` `TEXT NOT NULL`
- `is_active` `INTEGER NOT NULL DEFAULT 1`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`
- `created_at` `TEXT NOT NULL`
- `updated_at` `TEXT NOT NULL`

Indexes:

- unique index on `repo_root`

### `tasks`

Purpose:

- canonical planner-owned task record

Columns:

- `task_id` `TEXT PRIMARY KEY`
- `title` `TEXT NOT NULL`
- `summary` `TEXT NOT NULL`
- `objective_md` `TEXT NOT NULL`
- `context_md` `TEXT NOT NULL`
- `scope_md` `TEXT NOT NULL`
- `deliverables_md` `TEXT NOT NULL`
- `acceptance_md` `TEXT NOT NULL`
- `testing_md` `TEXT NOT NULL`
- `dispatch_md` `TEXT NOT NULL`
- `closeout_md` `TEXT NOT NULL`
- `reconciliation_md` `TEXT NOT NULL`
- `planner_status` `TEXT NOT NULL`
- `version` `INTEGER NOT NULL DEFAULT 1`
- `priority` `INTEGER NOT NULL`
- `task_type` `TEXT NOT NULL`
- `planner_owner` `TEXT NOT NULL`
- `worker_owner` `TEXT`
- `target_repo_id` `TEXT NOT NULL`
- `approval_required` `INTEGER NOT NULL DEFAULT 0`
- `source_kind` `TEXT NOT NULL DEFAULT 'planner'`
- `archived_at` `TEXT`
- `created_at` `TEXT NOT NULL`
- `updated_at` `TEXT NOT NULL`
- `closed_at` `TEXT`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `target_repo_id -> repos.repo_id`

Planner lifecycle values:

- `todo`
- `in_progress`
- `blocked`
- `done`

Indexes:

- index on `(planner_status, priority)`
- index on `(target_repo_id, planner_status)`
- index on `planner_owner`
- index on `worker_owner`
- index on `version`

### `task_execution_settings`

Purpose:

- execution policy separate from human task body

Columns:

- `task_id` `TEXT PRIMARY KEY`
- `task_kind` `TEXT NOT NULL`
- `sandbox_mode` `TEXT`
- `approval_policy` `TEXT`
- `additional_writable_dirs_json` `TEXT NOT NULL DEFAULT '[]'`
- `timeout_seconds` `INTEGER NOT NULL`
- `execution_metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `task_id -> tasks.task_id`

### `task_dependencies`

Purpose:

- dependency edges for eligibility traversal

Columns:

- `task_id` `TEXT NOT NULL`
- `depends_on_task_id` `TEXT NOT NULL`
- `dependency_kind` `TEXT NOT NULL DEFAULT 'hard'`
- `created_at` `TEXT NOT NULL`

Primary key:

- `(task_id, depends_on_task_id)`

Foreign keys:

- `task_id -> tasks.task_id`
- `depends_on_task_id -> tasks.task_id`

Indexes:

- index on `depends_on_task_id`

### `task_assignments`

Purpose:

- planner and worker assignment history, not active lease state

Columns:

- `assignment_id` `INTEGER PRIMARY KEY`
- `task_id` `TEXT NOT NULL`
- `assignee_kind` `TEXT NOT NULL`
- `assignee_id` `TEXT NOT NULL`
- `assignment_state` `TEXT NOT NULL`
- `assigned_at` `TEXT NOT NULL`
- `released_at` `TEXT`
- `notes` `TEXT`

Foreign keys:

- `task_id -> tasks.task_id`

Indexes:

- index on `(task_id, assignment_state)`
- index on `(assignee_kind, assignee_id, assignment_state)`

### `task_active_leases`

Purpose:

- first-class active claim state for workers and dispatchers

Columns:

- `task_id` `TEXT PRIMARY KEY`
- `lease_owner_kind` `TEXT NOT NULL`
- `lease_owner_id` `TEXT NOT NULL`
- `assignment_state` `TEXT NOT NULL`
- `lease_acquired_at` `TEXT NOT NULL`
- `lease_expires_at` `TEXT NOT NULL`
- `last_heartbeat_at` `TEXT`
- `execution_run_id` `TEXT`
- `lease_metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `task_id -> tasks.task_id`

Rules:

- at most one active lease row per task
- delete or archive the row when the lease is released, converted, or recovered as stale
- heartbeat renewal updates `lease_expires_at` and `last_heartbeat_at`
- `execution_run_id` is an optional run or attempt identifier for the current execution, not a second canonical task ID

Indexes:

- index on `assignment_state`
- index on `lease_expires_at`
- index on `(lease_owner_kind, lease_owner_id, assignment_state)`

### `task_runtime_state`

Purpose:

- first-class runtime state for the execution subsystem operating over CENTRAL DB

Columns:

- `task_id` `TEXT PRIMARY KEY`
- `runtime_status` `TEXT NOT NULL`
- `queue_name` `TEXT`
- `claimed_by` `TEXT`
- `claimed_at` `TEXT`
- `started_at` `TEXT`
- `finished_at` `TEXT`
- `pending_review_at` `TEXT`
- `last_runtime_error` `TEXT`
- `retry_count` `INTEGER NOT NULL DEFAULT 0`
- `last_transition_at` `TEXT NOT NULL`
- `runtime_metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `task_id -> tasks.task_id`

Runtime lifecycle values:

- `queued`
- `claimed`
- `running`
- `pending_review`
- `failed`
- `timeout`
- `canceled`
- `done`

Indexes:

- index on `(runtime_status, last_transition_at)`
- index on `(queue_name, runtime_status)`
- index on `claimed_by`

### `task_runtime_links`

Purpose:

- optional linkage table only when runtime identity must map to an external system or a transitional split-DB deployment
- this table is not required in the steady-state shared-DB model

Columns:

- `task_id` `TEXT PRIMARY KEY`
- `runtime_system` `TEXT NOT NULL`
- `runtime_task_id` `TEXT NOT NULL`
- `runtime_status` `TEXT`
- `last_synced_at` `TEXT`
- `sync_state` `TEXT NOT NULL DEFAULT 'active'`
- `sync_metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `task_id -> tasks.task_id`

Indexes:

- unique index on `(runtime_system, runtime_task_id)`

### `task_events`

Purpose:

- append-only planner and runtime audit trail

Columns:

- `event_id` `INTEGER PRIMARY KEY`
- `task_id` `TEXT NOT NULL`
- `event_type` `TEXT NOT NULL`
- `actor_kind` `TEXT NOT NULL`
- `actor_id` `TEXT NOT NULL`
- `payload_json` `TEXT NOT NULL DEFAULT '{}'`
- `created_at` `TEXT NOT NULL`

Foreign keys:

- `task_id -> tasks.task_id`

Indexes:

- index on `(task_id, created_at)`
- index on `(event_type, created_at)`

### `task_artifacts`

Purpose:

- structured links to closeout evidence, reports, exports, or result files

Columns:

- `artifact_id` `INTEGER PRIMARY KEY`
- `task_id` `TEXT NOT NULL`
- `artifact_kind` `TEXT NOT NULL`
- `path_or_uri` `TEXT NOT NULL`
- `label` `TEXT`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`
- `created_at` `TEXT NOT NULL`

Foreign keys:

- `task_id -> tasks.task_id`

Indexes:

- index on `(task_id, artifact_kind)`

## Required Field Mapping

Every required planner task field has a DB home:

- task id -> `tasks.task_id`
- status -> `tasks.planner_status`
- optimistic planner edit version -> `tasks.version`
- target repo -> `tasks.target_repo_id`
- objective -> `tasks.objective_md`
- context -> `tasks.context_md`
- scope and boundaries -> `tasks.scope_md`
- deliverables -> `tasks.deliverables_md`
- acceptance -> `tasks.acceptance_md`
- testing -> `tasks.testing_md`
- dependencies -> `task_dependencies`
- closeout contract -> `tasks.closeout_md`
- planner owner -> `tasks.planner_owner`
- worker owner -> `tasks.worker_owner`
- active lease owner -> `task_active_leases.lease_owner_kind`, `task_active_leases.lease_owner_id`
- active assignment state -> `task_active_leases.assignment_state`
- lease expiry and heartbeat -> `task_active_leases.lease_expires_at`, `task_active_leases.last_heartbeat_at`
- runtime state -> `task_runtime_state.runtime_status`
- runtime queue and transitions -> `task_runtime_state.queue_name`, `task_runtime_state.last_transition_at`
- execution settings -> `task_execution_settings`
- dispatch and reconciliation policy -> `tasks.dispatch_md`, `tasks.reconciliation_md`
- optional external runtime mapping -> `task_runtime_links`
- audit history -> `task_events`

## Generated Surfaces

Allowed generated surfaces:

- `CENTRAL/tasks.md` as portfolio summary
- optional markdown task exports for handoff or review
- dashboards or tabular reports

Rules:

- generated surfaces may be deleted and rebuilt from DB state
- no planner action should require editing generated markdown as the canonical step

## Migration From Current Markdown

Phase 1:

- treat existing `CENTRAL/tasks/*.md` and task packet docs as migration input only
- parse or manually import their fields into SQLite records
- record the original markdown path in `metadata_json` or a migration event

Phase 2:

- generate `tasks.md` from DB state
- stop creating new canonical task markdown files
- keep legacy markdown task files as archived snapshots or remove them after migration confidence is high

Phase 3:

- planner and operator tooling write directly to DB
- markdown exists only as export, report, or archival artifact

## Notes On Scalability

- dependency queries should run against indexed edge tables, not file graphs
- worker and planner concurrency should use transactional updates, not file rewrites
- planner writes should use `tasks.version` for optimistic concurrency checks
- active worker claims should be queryable from `task_active_leases` without reconstructing state from event logs
- runtime queue state should be queryable from `task_runtime_state` without inferring state from generic linkage rows
- summary views should be SQL queries or generated reports
- future dispatcher integration can read directly from this DB or from a synchronized runtime table without changing the planner source of truth
