-- Migration 0009: Metrics schema additions (METRICS-3)
--
-- (1) dispatcher_heartbeat_history: stores periodic dispatcher status snapshots
--     for time-series analysis of queue depth and concurrency.
-- (2) task_runtime_state.exit_code / exit_category: numeric exit code from worker
--     subprocess and its normalized category (success, timeout, quota,
--     operator_kill, code_error).
-- (3) task_runtime_state.tokens_used / tokens_cost_usd: optional token count and
--     cost captured from worker result payload (schema_version 2+).
--
-- All changes are backward-compatible: new columns default to NULL, existing rows
-- remain valid, and the heartbeat table starts empty.

-- -----------------------------------------------------------------------
-- (1) Dispatcher heartbeat history
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatcher_heartbeat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    active_workers INTEGER NOT NULL,
    max_workers INTEGER NOT NULL,
    queued_count INTEGER NOT NULL,   -- eligible tasks waiting to be claimed
    running_tasks TEXT NOT NULL DEFAULT '[]'  -- JSON array of active task IDs
);

CREATE INDEX IF NOT EXISTS idx_dispatcher_heartbeat_history_captured_at
ON dispatcher_heartbeat_history (captured_at);

-- -----------------------------------------------------------------------
-- (2) Exit code tracking on task_runtime_state
-- -----------------------------------------------------------------------

ALTER TABLE task_runtime_state ADD COLUMN exit_code INTEGER;
ALTER TABLE task_runtime_state ADD COLUMN exit_category TEXT
    CHECK (exit_category IS NULL OR exit_category IN (
        'success', 'timeout', 'quota', 'operator_kill', 'code_error'
    ));

-- -----------------------------------------------------------------------
-- (3) Token / cost tracking on task_runtime_state
-- -----------------------------------------------------------------------

ALTER TABLE task_runtime_state ADD COLUMN tokens_used INTEGER;
ALTER TABLE task_runtime_state ADD COLUMN tokens_cost_usd REAL;
