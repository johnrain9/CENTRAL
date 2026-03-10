# Dispatch And Status Snippets

## Planner Dispatch Message
```text
repo=CENTRAL do task <task_id>
```

## Dispatch Discipline
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.
- Treat ordered task lists as planner sequencing, not as a single worker handoff blob.
- Resolve the task from CENTRAL canonical state first; use `target_repo` to determine where execution belongs.

## Worker Closeout Message
```text
<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Reconciliation Discipline
- Reconcile worker results into CENTRAL canonical state first.
- Refresh generated summary views second.
- Update repo-local mirrors only if those mirrors are still intentionally maintained.

## Quick Replan Prompts
- "List all blocked CENTRAL tasks and propose unblockers."
- "Select one unblocked high-priority CENTRAL task per target repo and dispatch."
- "Given worker closeouts, update CENTRAL canonical state and propose the next 3 dispatches."
- "Refresh generated CENTRAL summary views and highlight drift against canonical state."
- "Convert this design doc into a CENTRAL canonical task with the right target repo and dependencies."
- "Bootstrap tracking for a new project in CENTRAL canonical state first."
