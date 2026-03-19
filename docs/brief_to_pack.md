# brief_to_pack — Design-brief to draft task-pack generator

`scripts/brief_to_pack.py` converts a structured design brief into a draft
task pack for planner review.  It expands each task stub using the same
templates as `task_quick.py`, resolves `depends_on: [previous]` to real task
IDs via a dry-run allocation, and presents the draft for review before any
tasks are committed to the DB.

## Why it exists

Large chunks of planner time go to repetitive decomposition: writing the same
boilerplate objective/context/scope/deliverables/acceptance/testing blocks for
every task in a workstream.  `brief_to_pack.py` automates that first pass.
The planner remains in control; no tasks are created until the review/edit
step is complete.

## Quick start

```bash
# 1. Write a brief (see format below or copy the example)
cp docs/examples/briefs/auth_overhaul.yaml /tmp/my_brief.yaml
$EDITOR /tmp/my_brief.yaml

# 2. Preview the draft (no DB writes)
python3 scripts/brief_to_pack.py --brief /tmp/my_brief.yaml

# 3. Save the expanded YAML for editing
python3 scripts/brief_to_pack.py --brief /tmp/my_brief.yaml --output /tmp/draft.yaml
$EDITOR /tmp/draft.yaml

# 4. Commit from the edited draft
python3 scripts/central_task_db.py task-batch-create --input /tmp/draft.yaml

# — or — commit directly from the brief with interactive confirmation
python3 scripts/brief_to_pack.py --brief /tmp/my_brief.yaml --commit

# — or — dry-run to preview IDs without writing
python3 scripts/brief_to_pack.py --brief /tmp/my_brief.yaml --commit --dry-run
```

## Brief format

```yaml
title: "Workstream name"       # used as a context prefix in the review output
repo: SOME_REPO                # default repo for all tasks (must be onboarded)
series: CENTRAL-OPS            # default task ID series
context: "..."                 # shared context prepended to every task's context
priority: 50                   # default priority override (optional)

tasks:
  - title: "Task one"
    template: design            # any task_quick template (see --list-templates)
    priority: 70                # per-task override (optional)
    context: "Extra context."   # appended to the brief-level context (optional)
    objective: "..."            # full field override (optional)
    scope: "..."
    deliverables: "..."
    acceptance: "..."
    testing: "..."
    depends_on: [previous]      # "previous" = the task immediately above

  - title: "Task two"
    template: feature
    depends_on: [previous]      # resolves to the real ID of task one
```

### depends_on: [previous]

The special value `previous` resolves to the task immediately above it in the
brief.  `brief_to_pack.py` runs a dry-run ID allocation before printing the
review, so the resolved IDs appear in the draft YAML exactly as they would be
written to the DB.

You can also list explicit task IDs alongside `previous`:

```yaml
depends_on: [previous, CENTRAL-OPS-12]
```

## Review/edit loop

```
brief_to_pack.py --brief FILE
    │
    ├─ expands task stubs with template defaults
    ├─ pre-allocates IDs (dry-run, no DB writes)
    ├─ prints human-readable summary + draft YAML
    │
    └─ [planner reviews / edits the YAML]
           │
           ├─ --output FILE → save draft, edit manually, then task-batch-create
           └─ --commit      → interactive confirm → task-batch-create
```

The planner always sees the full expanded YAML before any tasks hit the DB.
Use `--output` to save the draft for deeper editing, or `--commit` for
workstreams where the template defaults need little adjustment.

## Available templates

```bash
python3 scripts/task_quick.py --list-templates
```

Templates: `feature`, `bugfix`, `refactor`, `infrastructure`, `design`,
`docs`, `repo-health`, `validation`, `cleanup`, `planner-ops`.

## CLI reference

```
python3 scripts/brief_to_pack.py --help
```

| Flag | Description |
|------|-------------|
| `--brief FILE` | YAML brief path, or `-` for stdin. Default: stdin. |
| `--output FILE` | Write expanded draft YAML to this file. |
| `--commit` | After review, commit via `task-batch-create`. |
| `--dry-run` | With `--commit`: preview IDs, no DB writes. |
| `--yes` / `-y` | Skip interactive confirmation. |
| `--no-id-preview` | Skip dry-run pre-allocation (faster, but `previous` won't resolve). |

## Example

See `docs/examples/briefs/auth_overhaul.yaml` for a complete four-task
workstream brief.

```bash
python3 scripts/brief_to_pack.py --brief docs/examples/briefs/auth_overhaul.yaml
```
