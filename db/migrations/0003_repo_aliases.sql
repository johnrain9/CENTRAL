CREATE TABLE IF NOT EXISTS repo_aliases (
    alias_id INTEGER PRIMARY KEY,
    repo_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos (repo_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repo_aliases_repo_alias
ON repo_aliases (repo_id, alias);

CREATE INDEX IF NOT EXISTS idx_repo_aliases_normalized_alias
ON repo_aliases (normalized_alias);
