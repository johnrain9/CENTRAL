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

## Session Routing (ecosystem)

`ecosystem` has `session_persistence_enabled`. Tasks with `session_focus` in metadata route to a
session-persistent worker (resumes an experienced base session instead of cold-starting).

**Always set `--session-focus` for ecosystem implementation tasks:**

```bash
# Backend: Rust, API, DB, services
python3 scripts/task_quick.py --title "..." --repo ecosystem --series ECO --session-focus backend

# Frontend: UI, components, React
python3 scripts/task_quick.py --title "..." --repo ecosystem --series ECO --session-focus frontend
```

Rules:
- Audit tasks: **never** set `--session-focus` (audits always cold-start)
- CENTRAL / Dispatcher tasks: never set `--session-focus`
- Other repos: no sessions seeded yet — omit flag

## Backlog Scheduling

Tasks have a `schedule` metadata field controlling when they can dispatch:

| Value | Behavior |
|-------|----------|
| absent / `"anytime"` | Dispatches 24/7 (bugfix, investigation, validation) |
| `"backlog"` | Only dispatches during backlog windows (all other templates) |

**Backlog windows** (America/Denver, env-overridable via `CENTRAL_SCHEDULE_TIMEZONE`):
- Weekdays 10:00–16:00
- Every night 00:00–07:00

Outside these windows, only `anytime` tasks dispatch — keeping the product stable for manual testing.

```bash
# Override schedule at task creation
python3 scripts/task_quick.py --title "..." --repo ecosystem --schedule anytime   # urgent
python3 scripts/task_quick.py --title "..." --repo ecosystem --schedule backlog   # default for most templates
```

## Quick Replan Prompts
- "List all blocked CENTRAL tasks and propose unblockers."
- "Select one unblocked high-priority CENTRAL task per target repo and dispatch."
- "Given worker closeouts, update CENTRAL canonical state and propose the next 3 dispatches."
- "Refresh generated CENTRAL summary views and highlight drift against canonical state."
- "Convert this design doc into a CENTRAL canonical task with the right target repo and dependencies."
- "Bootstrap tracking for a new project in CENTRAL canonical state first."
