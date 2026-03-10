# CENTRAL Canonical Task System

This document defines the target canonical CENTRAL task model for scalable planning and dispatch.

## Direction

The long-term canonical source of truth is not markdown.

Target model:

- `CENTRAL` owns canonical task truth
- canonical task truth lives in a CENTRAL-managed SQLite database
- dispatcher and planner tooling operate against structured task records
- markdown surfaces are optional exports, summaries, templates, or migration aids only

Why this direction:

- hundreds or thousands of tasks require indexed querying, not file scanning
- multiple planner AIs and multiple workers need safe concurrent updates
- dependency traversal, prioritization, retry state, review queues, and assignment state belong in structured storage
- runtime and planning metadata must be queryable without markdown scraping heuristics

## Transitional State

The current repo contains markdown task files and summary boards because they were the bootstrap path.

Those surfaces should be treated as one of:

- migration scaffolding
- generated summaries
- human-readable exports
- temporary task-definition surfaces until DB-native authoring lands

They are not the desired end-state source of truth.

## Canonical Target Model

Canonical task records should live in a SQLite database managed under `CENTRAL`.

Suggested storage split:

- SQLite DB: canonical task definitions, dependencies, assignment, lifecycle, timestamps, and history pointers
- generated summaries: high-level human-readable portfolio views
- optional markdown exports: task snapshots or worker handoff views when useful

## Required Task Record Fields

Minimum canonical task fields:

- `task_id`
- `title`
- `status`
- `target_repo`
- `task_type`
- `priority`
- `planner_owner`
- `worker_owner`
- `summary`
- `objective`
- `context`
- `scope_boundaries`
- `deliverables`
- `acceptance`
- `testing`
- `dispatch_contract`
- `closeout_contract`
- `created_at`
- `updated_at`
- `closed_at`
- `metadata_json`

Dependency and execution settings should be normalized into related tables or structured JSON depending on final schema design.

## Scalability Requirements

The canonical task system must support:

- multiple planner AIs updating task state safely
- multiple workers claiming and executing tasks concurrently
- dependency-aware eligibility queries
- priority-based dispatch ordering
- auditable closeout and retry history
- generated summary views without making those views canonical

Design for the operating assumption that throughput will increase by 5-10x once multi-worker dispatch is active.

## Source Of Truth

Target source of truth:

- CENTRAL SQLite DB for planner-owned task definition and planner lifecycle state

Non-canonical surfaces:

- generated `tasks.md`
- exported markdown task cards
- repo-local mirrors or intake notes

If any generated surface disagrees with the DB, the DB wins.

## Dispatcher Model

Target model:

- planner creates and updates canonical tasks in CENTRAL DB
- dispatcher reads eligible work from structured task records, directly or through the autonomy runtime
- workers execute against the `target_repo` recorded on the task
- planner reconciles worker outcomes back into canonical DB state

Do not design around markdown file discovery as the steady-state runtime model.

## Relationship To Autonomy

Autonomy may remain the execution engine, but the planning contract must scale.

Valid end states include:

- autonomy runtime backed directly by CENTRAL DB
- a one-way or two-way sync between CENTRAL DB and autonomy runtime DB
- a unified DB with planner/runtime role separation

The specific integration choice must preserve CENTRAL-owned planning authority while avoiding markdown as the canonical storage layer.

## Migration Principle

Migration work should move from:

- markdown-authored canonical tasks

to:

- DB-authored canonical tasks with generated markdown only where helpful

Do not deepen investment in markdown-first task ingestion beyond what is needed to bridge the transition.
