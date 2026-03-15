# CENTRAL To Autonomy Integration Model

This document defines the selected DB-native integration model between CENTRAL planning state and autonomy runtime behavior.

## Selected Model

Choose a shared-DB model.

- CENTRAL SQLite DB is the canonical planner source of truth.
- autonomy is an execution subsystem operating over CENTRAL DB tables.
- planner and runtime state remain logically separate, but they live in one canonical DB model.
- markdown is not part of steady-state discovery, synchronization, or state mutation.

This rejects:

- markdown-first discovery
- long-term file scanning
- a duplicated canonical planner store inside a second runtime DB

## Why This Model

It scales better because it avoids:

- duplicated task definitions across two DBs
- eventual-consistency drift between planner DB and runtime DB
- secondary ID mapping as a core architectural requirement
- bridge logic becoming a permanent control plane

## Responsibilities

Planner surfaces own:

- task definition fields
- planner lifecycle state
- dependency edges
- repo targeting
- execution policy
- reconciliation after review or failure analysis

Runtime surfaces own:

- task claim and lease state
- runtime status transitions
- worker heartbeats
- run failure or timeout markers
- pending-review promotion and runtime evidence capture

## DB Tables Used By Role

Planner-owned primary tables:

- `tasks`
- `task_execution_settings`
- `task_dependencies`
- `repos`

Runtime-owned primary tables:

- `task_runtime_state`
- `task_active_leases`
- `task_events`
- `task_artifacts`

Optional integration table:

- `task_runtime_links`
  - only needed if a temporary external runtime or split-DB deployment still exists

## Dispatcher Discovery

Dispatcher should discover work from DB-native queries, not file scans.

Steady-state eligibility flow:

1. planner creates or updates a task in CENTRAL DB
2. planner lifecycle state is `todo` or `in_progress`
3. dependencies are satisfied in `task_dependencies`
4. runtime state is absent or reclaimable
5. dispatcher selects from CENTRAL DB query results
6. dispatcher atomically creates or updates `task_runtime_state` and `task_active_leases`

## Status Mapping

Planner lifecycle:

- `todo`
- `in_progress`
- `blocked`
- `done`

Runtime lifecycle:

- `queued`
- `claimed`
- `running`
- `pending_review`
- `failed`
- `timeout`
- `canceled`
- `done`

Rules:

- planner lifecycle answers whether work should exist and what the planner believes about its progress
- runtime lifecycle answers what the worker subsystem is doing now
- runtime `done` auto-reconciles planner lifecycle to `done` when the task does not require review
- `pending_review` does not auto-reconcile planner lifecycle
- planner reconciliation still handles review-required, blocked, failed, and timeout outcomes after inspecting runtime evidence

## Task Identity

- use one stable `task_id` across planner and runtime tables
- do not introduce a second mandatory task ID space
- if a temporary external runtime still requires a separate task identifier, store it in `task_runtime_links.runtime_task_id`
- if runtime execution needs a per-attempt or per-run identifier, treat that as execution metadata rather than a second task identity

## CLI And API Boundaries

Planner actions should be exposed through planner-facing commands or APIs that mutate planner-owned tables only.

Examples:

- create or update task definition
- set dependencies
- reprioritize or reassign
- mark blocked
- reconcile closeout after review

Runtime and dispatcher actions should mutate runtime-owned tables only.

Examples:

- claim eligible task
- renew lease heartbeat
- move to running
- mark pending review
- mark failed or timeout
- release stale lease

Review actions may touch both domains in sequence:

1. runtime evidence is inspected
2. runtime state is finalized
3. planner lifecycle is reconciled explicitly

## Deprecating The Transitional Bridge

The existing markdown-first bridge is compatibility-only.

Deprecation plan:

1. freeze features on the markdown bridge
2. stop expanding markdown-first task ingestion
3. implement DB-native planner and dispatcher surfaces
4. migrate bootstrap markdown tasks into DB records
5. retire `autonomy central sync` as a primary architecture path

`autonomy central sync` may remain temporarily for migration or import, but it is not the steady-state contract.

## A Newly Created Task Becoming Dispatchable

Steady-state flow:

1. planner inserts task row plus execution settings and dependencies in CENTRAL DB
2. planner lifecycle is `todo`
3. dependency query shows task eligible
4. dispatcher claims it by creating runtime state and lease rows transactionally
5. worker runs in the `target_repo` specified by planner-owned data

No markdown export or file discovery step is required.
