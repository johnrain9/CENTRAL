-- Migration 0005: Persist effective worker model and selection source on runtime state rows.
--
-- Operators need to audit which model a worker used after the fact, and why it was selected
-- (dispatcher default, task-class policy, or explicit task override).
--
-- effective_worker_model: the resolved model string that was passed to the worker
-- worker_model_source:    one of task_override | policy_default | dispatcher_default

ALTER TABLE task_runtime_state ADD COLUMN effective_worker_model TEXT;
ALTER TABLE task_runtime_state ADD COLUMN worker_model_source TEXT;
