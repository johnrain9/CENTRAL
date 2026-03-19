# Dependency-First Planning Discipline

Dependencies encode sequence in a machine-queryable graph. Prose descriptions of ordering belong in `context_md` for human readers, but the canonical sequencing constraint lives in the `task_dependencies` table. This document describes how to enforce that discipline.

## Why it matters

When tasks are created without declared dependency edges:

- `view-eligible` may surface a task that cannot actually start yet
- The dispatcher may claim work that will fail because its upstream is not done
- Planning debt accumulates as hidden sequencing lives only in prose

Declared edges eliminate ambiguity and make the scheduler correct by construction.

## Dependency capture at creation time

### planner-new

Always pass `--depends-on` for every known upstream task at scaffold time:

```sh
python3 scripts/central_task_db.py planner-new \
  --title "Implement rate limiter" \
  --depends-on CENTRAL-OPS-10 \
  --depends-on CENTRAL-OPS-11
```

If no `--depends-on` is provided, `planner-new` emits a stderr reminder to run `dep-lint` after creation.

### task-batch-create

Declare `dependencies` in each task item in the YAML batch file:

```yaml
tasks:
  - title: "First task"
    # no deps — this is the root
  - title: "Second task"
    dependencies:
      - CENTRAL-OPS-10   # wait for first task
```

### task-update

Add missing edges any time after creation:

```sh
python3 scripts/central_task_db.py task-update \
  --task-id CENTRAL-OPS-12 \
  --patch '{"dependencies": ["CENTRAL-OPS-10", "CENTRAL-OPS-11"]}'
```

Note: `task-update` with `dependencies` replaces the full edge set — include all deps, not just new ones.

## Inspecting the dependency graph

### dep-show — single task

Show what a task depends on and what depends on it:

```sh
python3 scripts/central_task_db.py dep-show --task-id CENTRAL-OPS-47
```

### dep-graph — full graph

Show dependency edges across all active tasks:

```sh
python3 scripts/central_task_db.py dep-graph
python3 scripts/central_task_db.py dep-graph --include-done   # include completed edges
python3 scripts/central_task_db.py dep-graph --json           # machine-readable
```

### dep-lint — missing edge detection

Scan task text fields for referenced task IDs with no declared edge:

```sh
python3 scripts/central_task_db.py dep-lint
```

Returns exit code 1 if any potential missing edges are found. Run this:
- After any batch creation
- Before dispatching a new tranche of work
- As a planning health check

## Workflow

1. **Create tasks** with `planner-new` or `task-batch-create`, declaring all known `--depends-on` edges.
2. **Run `dep-lint`** immediately after creation. Investigate every warning — some are false positives (tasks that merely reference another for context), but edges that represent real blocking relationships must be added.
3. **Use `dep-graph`** when sequencing a new work tranche to confirm the planned order is correctly encoded.
4. **Use `dep-show`** when investigating a specific task's readiness.
5. **Dispatch only unblocked work** — `view-eligible` filters on `dependency_blocked = false`, so declared edges feed directly into dispatch eligibility.

## Kinds of dependencies

The DB supports a `dependency_kind` field. Currently `hard` is the only kind in use:

| kind | meaning |
|------|---------|
| `hard` | Blocking: task cannot start until dependency reaches `done` |

Soft/advisory kinds may be added in future. Default to `hard` unless otherwise specified.

## False positives in dep-lint

`dep-lint` flags task IDs mentioned in text fields without a declared edge. Common non-blocking references that generate false positives:

- A task that *documents* another task's output without depending on it
- A closeout or reconciliation task that references its upstream by ID in prose

In these cases, no edge is needed. Confirm by reading the task context, then move on.
