# Batch Task Creation Reference

Use `task-batch-create` to create multiple CENTRAL tasks in a single operation from a YAML or JSON file.

## When to use

- Design passes that yield 3+ tasks at once.
- Seeding a new project phase with a consistent set of tasks.
- Any case where repeated `planner-new` + `task-create` calls add friction.

## Batch file formats

### YAML document (recommended)

```yaml
series: CENTRAL-OPS      # optional, default: CENTRAL-OPS
repo: CENTRAL             # optional, default: CENTRAL
defaults:                 # optional; merged under each item (item wins)
  priority: 9
  planner_status: todo
tasks:
  - title: "Implement feature X"
    objective: "Add X capability to the system."
    priority: 8           # overrides default

  - title: "Write integration tests for X"
    objective: "Cover X with end-to-end tests."
    depends_on:           # list of task IDs this task depends on
      - CENTRAL-OPS-42
```

### JSON document

```json
{
  "series": "CENTRAL-OPS",
  "repo": "CENTRAL",
  "defaults": {"priority": 9},
  "tasks": [
    {"title": "Implement feature X", "priority": 8},
    {"title": "Write integration tests for X"}
  ]
}
```

### Bare list (JSON or YAML)

```yaml
- title: "Task A"
  repo: CENTRAL
- title: "Task B"
  priority: 5
```

## Required fields per item

| Field   | Required | Notes                                      |
|---------|----------|--------------------------------------------|
| `title` | Yes      | May be supplied via `defaults`             |
| `repo`  | No       | Falls back to batch-level `repo` or CLI `--repo` |

All other fields are optional and receive sensible defaults (same as `planner-new`).

## CLI usage

```sh
# Dry run — preview IDs and validate without creating
python3 scripts/central_task_db.py task-batch-create \
  --input batch.yaml \
  --dry-run

# Create from YAML
python3 scripts/central_task_db.py task-batch-create \
  --input batch.yaml

# Create from JSON
python3 scripts/central_task_db.py task-batch-create \
  --input batch.json

# Override series and repo on the CLI
python3 scripts/central_task_db.py task-batch-create \
  --input batch.yaml \
  --series CENTRAL-OPS \
  --repo CENTRAL

# Pipe from stdin (JSON or YAML)
cat batch.yaml | python3 scripts/central_task_db.py task-batch-create --input -
```

## Output

Always JSON to stdout.

```json
{
  "status": "done",
  "dry_run": false,
  "total": 3,
  "created": 3,
  "failed": 0,
  "results": [
    {"index": 0, "task_id": "CENTRAL-OPS-75", "status": "created", "title": "Task A"},
    {"index": 1, "task_id": "CENTRAL-OPS-76", "status": "created", "title": "Task B"},
    {"index": 2, "task_id": "CENTRAL-OPS-77", "status": "created", "title": "Task C"}
  ]
}
```

`status` values: `done` (all succeeded), `partial` (some failed), `validation_failed` (pre-flight errors).

Exit code 0 on full success, 1 on any failure or validation error.

## Partial failures

Items that fail are reported with `"status": "error"` and an `"error"` string. The remaining items still proceed.

```json
{
  "status": "partial",
  "results": [
    {"index": 0, "task_id": "CENTRAL-OPS-75", "status": "created", "title": "Good task"},
    {"index": 1, "task_id": "CENTRAL-OPS-76", "status": "error", "error": "UNIQUE constraint failed: tasks.task_id"}
  ]
}
```

## ID allocation

- IDs are reserved as a contiguous range before any tasks are created.
- Items with an explicit `task_id` field skip allocation.
- Reservation is collision-safe: conflicts with existing tasks or active reservations fail immediately.
- Dry-run computes IDs deterministically without persisting a reservation.

## Examples directory

See `docs/examples/batch-tasks/` for ready-to-use batch files.
