# Dispatch And Status Snippets

## Planner Dispatch Message
```text
repo=<repo_name> do task <task_id>
```

## Dispatch Discipline
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.
- Treat ordered task lists as planner sequencing, not as a single worker handoff blob.
- For dependency-heavy repos, let the repo-local task board drive sequencing after each closeout.

## Repo Examples
```text
repo=video_queue do task T05
repo=aimSoloAnalysis do task TASK-P0-10
repo=ComfyUI do task T03
repo=ComfyUI path=/home/cobra/ComfyUI/user/workflows do task T11
```

## Worker Closeout Message
```text
<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Required In-File Status Update
Update the target task in the repo's task board (`tasks.md`, `TASKS.md`, or equivalent) before posting closeout:
- Set the repo-native status field or checkbox state to `done` or `blocked` equivalent
- Update acceptance and testing checkboxes/notes according to actual outcomes when the board format supports them
- If blocked: add one concrete unblocker request

## Quick Replan Prompts
- "List all `blocked` tasks across repos and propose unblockers."
- "Select one unblocked high-priority task per repo and dispatch."
- "Given worker closeouts, update priorities and next 3 dispatches."
- "Sync CENTRAL/tasks.md from repo-local task boards and highlight drift."
- "Convert this design doc into repo-local executable task IDs and update intake status."
- "Bootstrap tracking for a new project with no repo-local task board yet."
