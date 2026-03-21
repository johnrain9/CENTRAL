---
name: task-list
description: List and browse tasks from the CENTRAL task database. Triggers on "list tasks", "show tasks", "what tasks are there", "what's in the queue", "portfolio status", "eligible work", "blocked tasks", "what's running", "active tasks", "tasks for REPO".
---

# List Tasks

## Portfolio summary (start here)

```bash
python3 scripts/central_task_db.py view-summary
```

Counts by status across all repos.

## Eligible (dispatchable) work

```bash
python3 scripts/central_task_db.py view-eligible
```

Tasks that are todo/queued, unblocked, and ready to dispatch.

## Active / in-flight

```bash
python3 scripts/central_task_db.py view-active
```

Tasks in running, claimed, queued, blocked, or pending_review state.

## By repo

```bash
python3 scripts/central_task_db.py view-repo --repo REPO_ID
```

## Blocked work

```bash
python3 scripts/central_task_db.py view-blocked
```

## Raw list with filters

```bash
python3 scripts/central_task_db.py task-list --json
python3 scripts/central_task_db.py task-list --status todo --json
python3 scripts/central_task_db.py task-list --repo ecosystem --json
```

## Planner panel (comprehensive overview)

```bash
python3 scripts/central_task_db.py view-planner-panel
```

Shows summary, eligible work, blocked tasks, and active assignments in one view.

## Dispatcher status

```bash
python3 scripts/dispatcher_control.py status
```

Shows what's actively running in the dispatcher fleet.
