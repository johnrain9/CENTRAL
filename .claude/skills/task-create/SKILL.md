---
name: task-create
description: Create a new task in the CENTRAL task database. Triggers on any user request to create, add, file, or make a task — including "create a task for X", "add a task to fix Y", "file a task", "make a task", "we need a task for". DO NOT manually invoke task-preflight, planner-new, or task-create DB commands — use task_quick.py exclusively.
---

# Create a Task

**Always use `task_quick.py`. It handles scaffolding and the preflight ceremony automatically. Never run the manual ceremony.**

## Minimum command (2 required flags)

```bash
python3 scripts/task_quick.py --title "TITLE" --repo REPO_ID
```

## Common repos

| Alias | Canonical ID |
|-------|-------------|
| `CENTRAL` | CENTRAL (planner ops, tooling) |
| `ecosystem` | ecosystem (the Rust AI runtime) |
| `Dispatcher` | Dispatcher (autonomy/dispatch system) |

## Templates (default: `feature`)

```
feature        New capability (p50)
bugfix         Fix a defect (p70)
refactor       Structural improvement (p40)
infrastructure CI/CD, tooling, config (p60)
design         Design doc, not code (p30)
docs           Write/update docs (p35)
validation     Test/validate a system (p65)
cleanup        Remove dead code/artifacts (p45)
planner-ops    CENTRAL planner tooling (p50)
repo-health    Health adapter (p55)
```

List all templates: `python3 scripts/task_quick.py --list-templates`

## Optional overrides

```bash
--template TEMPLATE      # default: feature
--priority INT           # 0-100, higher = more urgent
--series SERIES          # default: CENTRAL-OPS  (use ECO for ecosystem tasks)
--initiative KEY         # epic/initiative grouping tag
--depends-on TASK_ID     # repeatable; adds dependency
--objective TEXT         # override the objective section
--scope TEXT             # override the scope section
--context TEXT           # override the context section
--novelty-rationale TEXT # why this is distinct from existing tasks (auto-generated if omitted)
--dry-run                # validate preflight without writing
```

## Examples

```bash
# Bare minimum
python3 scripts/task_quick.py --title "Fix OpenAI max_tokens field" --repo ecosystem

# With template and priority
python3 scripts/task_quick.py --title "Add dark mode" --repo ecosystem --template feature --priority 60

# Ecosystem bug with custom series
python3 scripts/task_quick.py --title "Fix websocket reconnect" --repo ecosystem --template bugfix --series ECO

# CENTRAL planner tooling
python3 scripts/task_quick.py --title "Add task-close skill" --repo CENTRAL --template planner-ops

# With dependency
python3 scripts/task_quick.py --title "Wire new provider" --repo ecosystem --depends-on ECO-103

# Dry run (validates without writing)
python3 scripts/task_quick.py --title "Test task" --repo CENTRAL --dry-run
```

## What happens automatically

1. Allocates the next task ID for the series
2. Builds a task scaffold from the template
3. Runs `task-preflight` using the exact same intent canonicalization that `task-create` verifies
4. Attaches the preflight token, classification, and override (if needed)
5. Calls `task-create` and prints the created task ID

## After creation

Show the user:
- The task ID (e.g. `ECO-103`)
- The dispatch message: `repo=REPO do task TASK_ID`
- Any blockers or caveats from the output

## If preflight blocks with `duplicate_blocked`

The preflight found a strongly overlapping existing task. Show the user the candidates and ask whether to proceed with `--novelty-rationale` explaining why this is distinct, or whether the existing task covers the need.
