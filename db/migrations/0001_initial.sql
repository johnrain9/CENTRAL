CREATE TABLE IF NOT EXISTS repos (
    repo_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    repo_root TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_repo_root
ON repos (repo_root);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    objective_md TEXT NOT NULL,
    context_md TEXT NOT NULL,
    scope_md TEXT NOT NULL,
    deliverables_md TEXT NOT NULL,
    acceptance_md TEXT NOT NULL,
    testing_md TEXT NOT NULL,
    dispatch_md TEXT NOT NULL,
    closeout_md TEXT NOT NULL,
    reconciliation_md TEXT NOT NULL,
    planner_status TEXT NOT NULL CHECK (planner_status IN ('todo', 'in_progress', 'blocked', 'done')),
    version INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    planner_owner TEXT NOT NULL,
    worker_owner TEXT,
    target_repo_id TEXT NOT NULL,
    approval_required INTEGER NOT NULL DEFAULT 0,
    source_kind TEXT NOT NULL DEFAULT 'planner',
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (target_repo_id) REFERENCES repos (repo_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_planner_status_priority
ON tasks (planner_status, priority);

CREATE INDEX IF NOT EXISTS idx_tasks_target_repo_status
ON tasks (target_repo_id, planner_status);

CREATE INDEX IF NOT EXISTS idx_tasks_planner_owner
ON tasks (planner_owner);

CREATE INDEX IF NOT EXISTS idx_tasks_worker_owner
ON tasks (worker_owner);

CREATE INDEX IF NOT EXISTS idx_tasks_version
ON tasks (version);

CREATE TABLE IF NOT EXISTS task_execution_settings (
    task_id TEXT PRIMARY KEY,
    task_kind TEXT NOT NULL,
    sandbox_mode TEXT,
    approval_policy TEXT,
    additional_writable_dirs_json TEXT NOT NULL DEFAULT '[]',
    timeout_seconds INTEGER NOT NULL,
    execution_metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL DEFAULT 'hard',
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends_on
ON task_dependencies (depends_on_task_id);

CREATE TABLE IF NOT EXISTS task_assignments (
    assignment_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    assignee_kind TEXT NOT NULL,
    assignee_id TEXT NOT NULL,
    assignment_state TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    released_at TEXT,
    notes TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_assignments_task_state
ON task_assignments (task_id, assignment_state);

CREATE INDEX IF NOT EXISTS idx_task_assignments_assignee_state
ON task_assignments (assignee_kind, assignee_id, assignment_state);

CREATE TABLE IF NOT EXISTS task_active_leases (
    task_id TEXT PRIMARY KEY,
    lease_owner_kind TEXT NOT NULL,
    lease_owner_id TEXT NOT NULL,
    assignment_state TEXT NOT NULL,
    lease_acquired_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    last_heartbeat_at TEXT,
    execution_run_id TEXT,
    lease_metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_active_leases_state
ON task_active_leases (assignment_state);

CREATE INDEX IF NOT EXISTS idx_task_active_leases_expires_at
ON task_active_leases (lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_task_active_leases_owner_state
ON task_active_leases (lease_owner_kind, lease_owner_id, assignment_state);

CREATE TABLE IF NOT EXISTS task_runtime_state (
    task_id TEXT PRIMARY KEY,
    runtime_status TEXT NOT NULL CHECK (
        runtime_status IN ('queued', 'claimed', 'running', 'pending_review', 'failed', 'timeout', 'canceled', 'done')
    ),
    queue_name TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    pending_review_at TEXT,
    last_runtime_error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_transition_at TEXT NOT NULL,
    runtime_metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_state_status_transition
ON task_runtime_state (runtime_status, last_transition_at);

CREATE INDEX IF NOT EXISTS idx_task_runtime_state_queue_status
ON task_runtime_state (queue_name, runtime_status);

CREATE INDEX IF NOT EXISTS idx_task_runtime_state_claimed_by
ON task_runtime_state (claimed_by);

CREATE TABLE IF NOT EXISTS task_runtime_links (
    task_id TEXT PRIMARY KEY,
    runtime_system TEXT NOT NULL,
    runtime_task_id TEXT NOT NULL,
    runtime_status TEXT,
    last_synced_at TEXT,
    sync_state TEXT NOT NULL DEFAULT 'active',
    sync_metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_runtime_links_external
ON task_runtime_links (runtime_system, runtime_task_id);

CREATE TABLE IF NOT EXISTS task_events (
    event_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_created
ON task_events (task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_task_events_type_created
ON task_events (event_type, created_at);

CREATE TABLE IF NOT EXISTS task_artifacts (
    artifact_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    path_or_uri TEXT NOT NULL,
    label TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_kind
ON task_artifacts (task_id, artifact_kind);
