# Dispatch And Status Snippets

## Planner Dispatch Message
```text
repo=<repo_name> do task Txx
```

## Worker Closeout Message
```text
Txx | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Required In-File Status Update
Update the target task in `tasks.md` before posting closeout:
- `## Status`: set to `done` or `blocked`
- Acceptance and testing checkboxes: check/uncheck according to actual outcomes
- If blocked: add one concrete unblocker request

## Quick Replan Prompts
- "List all `blocked` tasks across repos and propose unblockers."
- "Select one unblocked high-priority task per repo and dispatch."
- "Given worker closeouts, update priorities and next 3 dispatches."
- "Sync CENTRAL/tasks.md from repo-local tasks.md files and highlight drift."
- "Convert this design doc into repo-local Txx tasks and update intake status."
- "Bootstrap tracking for a new project with no tasks.md yet."
