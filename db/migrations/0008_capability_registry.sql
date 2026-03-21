CREATE TABLE IF NOT EXISTS capabilities (
    capability_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'active', 'deprecated')),
    kind TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('local', 'cross_repo_contract', 'workflow')),
    owning_repo_id TEXT NOT NULL,
    when_to_use_md TEXT NOT NULL,
    do_not_use_for_md TEXT NOT NULL DEFAULT '',
    entrypoints_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    evidence_summary_md TEXT NOT NULL DEFAULT '',
    verification_level TEXT NOT NULL CHECK (verification_level IN ('provisional', 'planner_verified', 'audited')),
    verified_by_task_id TEXT,
    replaced_by_capability_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
        (status = 'proposed' AND verification_level IN ('provisional', 'planner_verified'))
        OR (status = 'active' AND verification_level IN ('provisional', 'planner_verified', 'audited'))
        OR (status = 'deprecated' AND verification_level IN ('planner_verified', 'audited'))
    ),
    CHECK (replaced_by_capability_id IS NULL OR replaced_by_capability_id <> capability_id),
    FOREIGN KEY (owning_repo_id) REFERENCES repos (repo_id),
    FOREIGN KEY (verified_by_task_id) REFERENCES tasks (task_id),
    FOREIGN KEY (replaced_by_capability_id) REFERENCES capabilities (capability_id)
);

CREATE INDEX IF NOT EXISTS idx_capabilities_status_kind
ON capabilities (status, kind);

CREATE INDEX IF NOT EXISTS idx_capabilities_owning_repo_status
ON capabilities (owning_repo_id, status);

CREATE INDEX IF NOT EXISTS idx_capabilities_verification_status
ON capabilities (verification_level, status);

CREATE TABLE IF NOT EXISTS capability_affected_repos (
    capability_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (capability_id, repo_id),
    FOREIGN KEY (capability_id) REFERENCES capabilities (capability_id),
    FOREIGN KEY (repo_id) REFERENCES repos (repo_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_affected_repos_repo_id
ON capability_affected_repos (repo_id);

CREATE TABLE IF NOT EXISTS capability_source_tasks (
    capability_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    relationship_kind TEXT NOT NULL CHECK (
        relationship_kind IN ('created_by', 'updated_by', 'deprecated_by', 'superseded_by', 'seeded_from')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (capability_id, task_id, relationship_kind),
    FOREIGN KEY (capability_id) REFERENCES capabilities (capability_id),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_source_tasks_task_id
ON capability_source_tasks (task_id);

CREATE TABLE IF NOT EXISTS capability_events (
    event_id INTEGER PRIMARY KEY,
    capability_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (capability_id) REFERENCES capabilities (capability_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_events_capability_created
ON capability_events (capability_id, created_at);

CREATE INDEX IF NOT EXISTS idx_capability_events_type_created
ON capability_events (event_type, created_at);

CREATE INDEX IF NOT EXISTS idx_capability_events_actor_created
ON capability_events (actor_id, created_at);

CREATE TABLE IF NOT EXISTS task_creation_preflight (
    preflight_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    preflight_revision TEXT NOT NULL,
    preflight_token TEXT NOT NULL,
    preflight_request_json TEXT NOT NULL,
    preflight_response_json TEXT NOT NULL,
    query_text TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (
        classification IN ('new', 'follow_on', 'extends_existing', 'supersedes', 'duplicate_do_not_create')
    ),
    novelty_rationale TEXT NOT NULL,
    override_reason TEXT,
    override_kind TEXT NOT NULL DEFAULT 'none' CHECK (
        override_kind IN ('none', 'weak_overlap', 'strong_overlap_privileged', 'bootstrap_bypass')
    ),
    related_task_ids_json TEXT NOT NULL DEFAULT '[]',
    related_capability_ids_json TEXT NOT NULL DEFAULT '[]',
    matched_task_ids_json TEXT NOT NULL DEFAULT '[]',
    matched_capability_ids_json TEXT NOT NULL DEFAULT '[]',
    blocking_bucket TEXT NOT NULL CHECK (blocking_bucket IN ('none', 'weak_overlap', 'strong_overlap', 'duplicate')),
    strong_overlap_count INTEGER NOT NULL DEFAULT 0,
    override_allowed INTEGER NOT NULL DEFAULT 0,
    performed_at TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_creation_preflight_task_version
ON task_creation_preflight (task_id, task_version);

CREATE INDEX IF NOT EXISTS idx_task_creation_preflight_classification_performed
ON task_creation_preflight (classification, performed_at);

CREATE INDEX IF NOT EXISTS idx_task_creation_preflight_bucket_performed
ON task_creation_preflight (blocking_bucket, performed_at);

CREATE TABLE IF NOT EXISTS capability_mutation_applications (
    application_key TEXT PRIMARY KEY,
    source_task_id TEXT NOT NULL,
    source_task_version INTEGER NOT NULL,
    mutation_digest TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'replayed')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_task_id) REFERENCES tasks (task_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_mutation_applications_source_task
ON capability_mutation_applications (source_task_id, source_task_version);
