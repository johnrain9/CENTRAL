-- Migration 0012: add session locking for resume-in-place mode
--
-- When session_resume_mode is enabled on a repo, only one worker at a time
-- can hold a given session (repo+focus). locked_by_task_id tracks which
-- task currently holds the session; locked_at enables stale lock cleanup.

ALTER TABLE session_registry ADD COLUMN locked_by_task_id TEXT DEFAULT NULL;
ALTER TABLE session_registry ADD COLUMN locked_at TEXT DEFAULT NULL;
