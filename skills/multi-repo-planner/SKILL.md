---
name: multi-repo-planner
description: Centralized planning and dispatch for multiple active repositories using task IDs in repo-local tasks.md files plus a central mirror board (for example CENTRAL/tasks.md). Use when coordinating 2+ repos, sequencing dependencies, onboarding new/non-git projects, converting design docs into dispatchable Txx tasks, assigning worker tasks, running portfolio reprioritization, and reconciling worker outcomes back into repo-local tasks.md status fields.
---

# Multi Repo Planner

## Planner Workflow
1. Collect state from each repo's `tasks.md`.
2. Collect central mirror state if present (for example `CENTRAL/tasks.md`) and identify drift.
3. Convert any new design intake into dispatchable repo-local `Txx` tasks.
4. Select highest-priority unblocked tasks and identify dependencies.
5. Dispatch worker tasks using `Txx` IDs.
6. Reconcile worker results and verify repo-local `tasks.md` status updates.
7. Re-sync central mirror from repo-local sources of truth.
8. Re-plan based on new blockers, failures, and completions.

## Dispatch Contract
- Minimal dispatch: `do task Txx`
- Explicit dispatch when needed: `repo=<repo_name> do task Txx`
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.

## Worker Contract
- Optional kickoff line: `Txx | in_progress | ref: <branch-or-context>`
- Required closeout line: `Txx | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>`
- Required file update on completion in the target repo's `tasks.md`:
  - Update `## Status` to `done` or `blocked`.
  - Update relevant Acceptance Criteria and Testing checkboxes.
  - If blocked, add exactly one concrete unblocker request.

## Status Discipline
- Allowed values: `todo`, `in_progress`, `blocked`, `done`.
- Keep no more than one `in_progress` task per worker.
- Treat tasks stuck in `blocked` for more than one planning cycle as replanning candidates.
- In divergence between central mirror and repo-local `tasks.md`, repo-local `tasks.md` wins, then mirror is refreshed.

## Central Mirror Discipline
- Keep central board as an aggregate view only; do not treat it as execution truth while repo-local `tasks.md` exists.
- Update central mirror on each planning cycle:
  - `Last sync` timestamp
  - per-repo counts (`done`, `in_progress`, `todo`)
  - active queue (all non-`done` tasks)
  - design intake conversion status
- Reflect exact task IDs from repo-local files; avoid ad-hoc status labels that cannot map back.

## New Project Intake
- If a project has no git repo or no `tasks.md`, add a central bootstrap task for that project.
- When requested, initialize a minimal repo-local `tasks.md` using `Txx` shape so the project can join dispatch/reconcile workflow.
- Track non-git projects explicitly in central snapshots (for example `non-git workspace`).

## Design Intake Conversion
- Treat major design docs as intake, not executable tasks.
- Convert intake into repo-local `Txx` tasks before dispatching work.
- Ensure converted tasks include objective, deliverables, acceptance, and testing sections.
- Mark intake state in central board (`proposal` -> `converted` -> `in_progress`/`done`).

## Required Task Shape
For this skill to work reliably, each task should include:
- `## Task Txx: ...`
- `## Repo`
- `## Status`
- `## Objective`
- `## Deliverables`
- `## Acceptance Criteria`
- `## Comprehensive Testing Requirements`

Use [references/dispatch-and-status.md](references/dispatch-and-status.md) for reusable planner snippets.
