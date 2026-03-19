-- Migration 0006: add optional initiative column to tasks
-- Tasks can now carry an initiative/epic tag (e.g. 'dispatcher-infrastructure',
-- 'voice-transcription', 'repo-health') so the planner can group and filter
-- work by feature area without relying solely on task ID series.

ALTER TABLE tasks ADD COLUMN initiative TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_initiative ON tasks (initiative);
