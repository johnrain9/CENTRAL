# CENTRAL To Autonomy Integration Model

This document defines the transitional markdown-first bridge model for how canonical `CENTRAL` tasks become dispatcher-consumable autonomy tasks. It is not the final DB-canonical architecture.

## Model

- This document describes a transitional bridge from markdown-authored CENTRAL tasks into autonomy DB.
- It remains useful for bootstrap and migration, but should be superseded by DB-canonical CENTRAL planning.
- The dispatcher does not read markdown directly.
- A bridge command syncs canonical `CENTRAL` tasks into autonomy DB records during the transition.

## Discovery

Bridge discovery rules:

- scan `CENTRAL/tasks/*.md`
- ignore `TASK_TEMPLATE.md`
- parse canonical task files that match the required schema
- use the task file itself as the source of truth for definition fields

Dispatcher discovery rules:

- dispatcher reads only autonomy DB tasks
- eligible work exists in autonomy because the bridge synced it from `CENTRAL`

This is the answer to: "How does dispatcher know what work exists if tasks live in CENTRAL?"

- planner authors tasks in `CENTRAL`
- bridge syncs them into autonomy
- dispatcher consumes autonomy DB state

## ID Mapping

- `CENTRAL` task ID maps 1:1 to autonomy task ID
- the bridge creates autonomy tasks with the same custom ID
- no secondary ID translation layer is introduced in this phase

## Status Model

Planning source of truth:

- `CENTRAL` status remains authoritative for planner lifecycle: `todo`, `in_progress`, `blocked`, `done`

Runtime source of truth:

- autonomy owns runtime states such as `pending`, `claimed`, `running`, `pending_review`, `failed`, `timeout`, `done`

Bridge status behavior:

- `todo` -> create or normalize autonomy task to `pending` if the task is not already active
- `in_progress` -> keep autonomy task dispatchable; do not clobber active runtime states
- `blocked` -> new autonomy tasks are created as `draft`; existing runtime tasks are not forced backward through invalid status transitions
- `done` -> do not create a new autonomy task; leave existing runtime record intact for audit

The bridge is definition-first. It does not overwrite active runtime states during sync.

## Field Mapping

Canonical task file to autonomy DB:

- `Task ID` -> autonomy task id
- title heading -> autonomy title
- `Target Repo` -> `repo_root`
- `Task Type` -> autonomy `category` using:
  - `implementation` -> `implementation`
  - `truth` -> `truth`
  - `planning|ops|docs|migration` -> `infrastructure`
- `Priority` -> `priority`
- `Task Kind` -> `task_kind`
- `Sandbox Mode` -> `sandbox_mode`
- `Approval Policy` -> `approval_policy`
- `Additional Writable Dirs` -> `additional_writable_dirs_json`
- `Timeout Seconds` -> `timeout_seconds`
- `Approval Required` -> `approval_required`
- `Testing` -> `validation_commands_json`
- canonical file path -> `design_doc_path`
- assembled task body sections -> `prompt_body`

## Dependencies

- Canonical dependency list in `CENTRAL` is the authored dependency surface.
- Dependencies that name canonical task IDs become autonomy DB dependency edges.
- External dependencies remain text in the canonical file and prompt body; they are not materialized as DB edges.

## Repo Targeting And Writable Dirs

- `Target Repo` defines the autonomy `repo_root`.
- `Additional Writable Dirs` defines any extra writable paths required beyond the target repo.
- Tasks that only read should use `Task Kind: read_only`.
- Mutating tasks should use `Task Kind: mutating` and keep writable scope explicit.

## Conflict And Reconciliation Rules

Sync direction in this phase:

- canonical definition fields flow from `CENTRAL` to autonomy
- runtime execution results do not automatically rewrite `CENTRAL`

Planner reconciliation in this phase:

- planner reviews autonomy runtime state and worker closeout
- planner updates the canonical `CENTRAL` task file and summary index
- repo-local mirrors are updated only after `CENTRAL` is correct

Do not treat autonomy runtime status as permission to silently rewrite planner status in markdown.

## Bridge Scope

This phase implements:

- one-way definition sync from `CENTRAL` to autonomy
- stable task ID reuse
- idempotent create or update behavior
- dependency synchronization for canonical task IDs

This phase does not implement:

- automatic closeout reconciliation back into markdown
- retirement of repo-local mirrors
- direct dispatcher reads from markdown
