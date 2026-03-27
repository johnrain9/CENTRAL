CREATE TABLE IF NOT EXISTS session_registry (
    registry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL REFERENCES repos(repo_id),
    session_id TEXT NOT NULL,
    session_name TEXT,
    status TEXT NOT NULL DEFAULT 'seeding'
        CHECK (status IN ('seeding', 'active', 'stale', 'retired')),
    seed_started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    seed_completed_at TEXT,
    last_forked_at TEXT,
    fork_count INTEGER NOT NULL DEFAULT 0,
    context_tokens INTEGER,
    seed_model TEXT,
    seed_cwd TEXT NOT NULL,
    seed_prompt_hash TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_registry_repo_active
ON session_registry (repo_id) WHERE status = 'active';
