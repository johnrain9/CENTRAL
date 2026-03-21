---
name: task-view
description: View details of a specific task from the CENTRAL task database. Triggers on "show task X", "view task X", "what is task X", "task X details", "tell me about TASK-ID". Also use for task card export.
argument-hint: TASK-ID
---

# View a Task

## Full task detail (most useful)

```bash
python3 scripts/central_task_db.py task-show --task-id TASK_ID
```

Shows: title, status, priority, objective, scope, deliverables, acceptance, dependencies, recent events.

## Formatted markdown task card

```bash
python3 scripts/central_task_db.py view-task-card --task-id TASK_ID
```

Good for sharing or dispatching to a worker.

## Runtime status (is it running/claimed/queued?)

```bash
python3 scripts/central_task_db.py view-active
```

Then filter visually or:
```bash
python3 scripts/central_task_db.py task-show --task-id TASK_ID
```
The runtime status appears in the task-show output.

## $ARGUMENTS

Run `task-show` for the task ID passed as `$ARGUMENTS`:

```bash
python3 scripts/central_task_db.py task-show --task-id $ARGUMENTS
```

Present the output clearly. Highlight: status, priority, objective (1 sentence), and any open blockers or dependencies.
