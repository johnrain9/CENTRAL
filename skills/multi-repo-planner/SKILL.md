---
name: multi-repo-planner
description: Centralized planning and dispatch for multiple active repositories using stable task IDs in repo-local task boards (`tasks.md`, `TASKS.md`, or equivalent) plus a central mirror board (for example CENTRAL/tasks.md). Use when coordinating 2+ repos, sequencing dependencies, onboarding new/non-git projects, converting design docs into dispatchable repo-native task IDs (for example `T05` or `TASK-P0-10`), assigning worker tasks, running portfolio reprioritization, and reconciling worker outcomes back into repo-local task-board status fields.
---

# Multi Repo Planner

## Known High-Activity Repo Targets
- `video_queue`: `/home/cobra/video_queue`
- `ComfyUI`: `/home/cobra/ComfyUI`
- `ComfyUI workflows focus path`: `/home/cobra/ComfyUI/user/workflows`

## Planner Workflow
1. Collect state from each repo's task board (`tasks.md`, `TASKS.md`, or repo-specific equivalent).
2. Collect central mirror state if present (for example `CENTRAL/tasks.md`) and identify drift.
3. Convert any new design intake into dispatchable repo-local task IDs using the repo's native naming scheme.
4. Select highest-priority unblocked tasks and identify dependencies.
5. Dispatch worker tasks using repo-native task IDs.
6. Reconcile worker results and verify repo-local task-board status updates.
7. Re-sync central mirror from repo-local sources of truth.
8. Re-plan based on new blockers, failures, and completions.

## Dispatch Contract
- Minimal dispatch: `do task <task_id>`
- Explicit dispatch when needed: `repo=<repo_name> do task <task_id>`
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.

## Worker Contract
- Optional kickoff line: `<task_id> | in_progress | ref: <branch-or-context>`
- Required closeout line: `<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>`
- Required file update on completion in the target repo's task board:
  - Update the repo-native status field or checkbox state to `done` or `blocked` equivalent.
  - Update relevant acceptance and testing checkboxes/notes when the board format supports them.
  - If blocked, add exactly one concrete unblocker request.

## Status Discipline
- Allowed values: `todo`, `in_progress`, `blocked`, `done`.
- Keep no more than one `in_progress` task per worker.
- Treat tasks stuck in `blocked` for more than one planning cycle as replanning candidates.
- In divergence between central mirror and repo-local task board, repo-local task board wins, then mirror is refreshed.

## Central Mirror Discipline
- Keep central board as an aggregate view only; do not treat it as execution truth while repo-local task boards exist.
- Update central mirror on each planning cycle:
  - `Last sync` timestamp
  - per-repo counts (`done`, `in_progress`, `todo`)
  - active queue (all non-`done` tasks)
  - design intake conversion status
- Reflect exact task IDs from repo-local files; avoid ad-hoc status labels that cannot map back.

## New Project Intake
- If a project has no git repo or no task board file, add a central bootstrap task for that project.
- When requested, initialize a minimal repo-local task board using the repo's preferred ID scheme so the project can join dispatch/reconcile workflow.
- Track non-git projects explicitly in central snapshots (for example `non-git workspace`).
- For workflow-only work in `ComfyUI/user/workflows`, allow central tracking tasks that reference concrete files in that directory.

## Design Intake Conversion
- Treat major design docs as intake, not executable tasks.
- Convert intake into repo-local executable task IDs before dispatching work.
- Ensure converted tasks include objective, deliverables, acceptance, and testing sections.
- Mark intake state in central board (`proposal` -> `converted` -> `in_progress`/`done`).

## Supported Task Board Shapes
Preferred shape:
- stable task IDs (for example `T05`, `TASK-P0-10`)
- explicit status field or checkbox state
- objective/deliverables/acceptance/testing details

This skill also supports lighter task boards when they still provide:
- a stable task identifier
- a current status (`todo`, `in-progress`, `blocked`, `done`, or checkbox equivalent)
- enough nearby task text to execute and verify the work

Use [references/dispatch-and-status.md](references/dispatch-and-status.md) for reusable planner snippets.
