-- Migration 0013: tag sessions with the backend that created them
--
-- Prevents Claude sessions being validated by the Codex adapter (and vice versa).
-- Existing rows default to 'claude' since all sessions were seeded via the Claude CLI.

ALTER TABLE session_registry ADD COLUMN seed_backend TEXT NOT NULL DEFAULT 'claude';

CREATE INDEX idx_session_registry_repo_focus_backend
    ON session_registry (repo_id, focus, seed_backend) WHERE status = 'active';
