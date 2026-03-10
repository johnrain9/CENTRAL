---
name: multi-repo-planner
description: Centralized planning and dispatch for multiple active repositories using CENTRAL as the canonical task system, with a DB-backed source of truth as the target operating model. Use when coordinating 2+ repos, sequencing dependencies, converting design docs into CENTRAL-owned tasks, dispatching worker work, running portfolio reprioritization, and reconciling worker outcomes back into CENTRAL.
---

# Multi Repo Planner

## Operating Assumption

Design for scale early.

Expected trajectory:

- one planner AI becomes multiple planners
- one worker becomes multiple workers running concurrently
- dispatch throughput increases substantially once the dispatcher fleet is active

Planning decisions, task storage, dispatch contracts, and reconciliation workflows must be chosen for multi-planner, multi-worker operation rather than single-user convenience.

## Canonical Model

Target source of truth:

- CENTRAL-managed structured task records in a DB-backed system

Transitional surfaces:

- generated summaries such as `tasks.md`
- exported markdown task cards when useful for human review or worker handoff
- repo-local mirrors or intake notes when helpful

Do not assume markdown files are the long-term canonical task store.

## Planner Workflow
1. Collect canonical planner-owned task state from CENTRAL’s canonical task system.
2. Consult repo-local boards only for roadmap context, local intake, or drift checks.
3. Convert new design intake into CENTRAL-owned canonical tasks with explicit `target_repo` and dependencies.
4. Select highest-priority unblocked work using structured task data, not incidental file ordering.
5. Dispatch one task per worker unless a worker explicitly supports a queue.
6. Reconcile worker results back into CENTRAL canonical state first.
7. Refresh generated summaries and any optional mirrors.
8. Re-plan continuously based on blockers, failures, completions, and queue pressure.

## Dispatch Contract
- Minimal dispatch: `repo=CENTRAL do task <task_id>`
- The worker resolves the task from CENTRAL canonical state.
- `target_repo` inside the task determines where implementation work belongs.
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.

## Worker Contract
- Optional kickoff line: `<task_id> | in_progress | ref: <branch-or-context>`
- Required closeout line: `<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>`
- Planner updates CENTRAL canonical state first, then any generated views or repo-local mirrors.
- If blocked, record exactly one concrete unblocker request.

## Status Discipline
- Keep status values explicit and machine-queryable.
- Prefer lifecycle models that can support review and reconciliation states as scale increases.
- Keep no more than one actively claimed task per worker unless the worker model explicitly supports batching.
- Treat stale blocked or stale in-progress work as replanning candidates.

## New Project Intake
- Add canonical CENTRAL tracking for any new repo or non-git project.
- Treat repo-local task boards as optional local views, not required execution truth.
- Track workflow-only or non-repo work as canonical CENTRAL tasks with explicit paths.

## Design Intake Conversion
- Treat major design docs as intake, not executable work.
- Convert intake into CENTRAL-owned canonical tasks before dispatching implementation.
- Ensure each task includes objective, context, scope, deliverables, acceptance, and testing requirements.
- Keep repo-local mirrors optional.

Use [references/dispatch-and-status.md](references/dispatch-and-status.md) for reusable planner snippets.
