-- Migration 0011: add focus column to session_registry
--
-- Allows multiple seeds per repo scoped by focus (e.g. 'frontend', 'backend',
-- 'other'). Empty string '' is the unfocused/legacy sentinel — backward
-- compatible with existing rows, which get focus='' by default.
--
-- The previous unique index only allowed one active session per repo.
-- It is replaced with one scoped to (repo_id, focus) so that a frontend
-- and backend seed can both be active for the same repo simultaneously.

ALTER TABLE session_registry ADD COLUMN focus TEXT NOT NULL DEFAULT '';

DROP INDEX IF EXISTS uq_session_registry_active_repo;

CREATE UNIQUE INDEX uq_session_registry_active_repo_focus
    ON session_registry (repo_id, focus) WHERE status = 'active';
