-- Migration 0004: repo health snapshot persistence
-- Stores point-in-time health bundles per repo with freshness semantics.
-- A snapshot is "stale" when NOW() > captured_at + ttl_seconds.

CREATE TABLE repo_health_snapshots (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id        TEXT    NOT NULL,
    captured_at    TEXT    NOT NULL,  -- ISO8601 UTC when the checks ran
    ttl_seconds    INTEGER NOT NULL DEFAULT 3600,
    working_status TEXT    NOT NULL,  -- pass | warn | fail | unknown
    evidence_quality TEXT  NOT NULL,  -- strong | partial | weak | none
    overall_status TEXT    NOT NULL,  -- pass | warn | fail | unknown
    adapter_name   TEXT    NOT NULL,
    adapter_version TEXT   NOT NULL,
    profile        TEXT    NOT NULL,
    report_json    TEXT    NOT NULL,  -- full per-repo JSON report
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_rhs_repo_captured
    ON repo_health_snapshots (repo_id, captured_at DESC);

CREATE INDEX idx_rhs_captured
    ON repo_health_snapshots (captured_at DESC);
