# task_quick — Streamlined Task Creation

`scripts/task_quick.py` is a planner-facing wrapper around `planner-new` + `task-create` that reduces task creation to 2 required flags.

## Minimum Usage

```sh
python3 scripts/task_quick.py --title "Fix login bug" --repo MOTO_HELPER
```

That's it. The tool picks the `feature` template by default, allocates the next `CENTRAL-OPS` task ID, fills all required fields from the template, and persists the task to the DB.

## Templates

| Template | task_type | Default Priority | Use When |
|---|---|---|---|
| `feature` (default) | feature | 50 | New capability or behavior |
| `bugfix` | bugfix | 70 | Diagnosed or reported defect |
| `refactor` | refactor | 40 | Code quality improvement, no behavior change |
| `infrastructure` | infrastructure | 60 | Tooling, CI, config, or platform work |

```sh
python3 scripts/task_quick.py --list-templates   # show all templates with details
```

## Examples

```sh
# Feature (default template)
python3 scripts/task_quick.py --title "Add export API" --repo AIM_SOLO_ANALYSIS

# Bugfix with explicit template
python3 scripts/task_quick.py --title "Fix null pointer in parser" --repo MOTO_HELPER --template bugfix

# Refactor with priority override
python3 scripts/task_quick.py --title "Refactor DB layer" --repo CENTRAL --template refactor --priority 55

# Infrastructure
python3 scripts/task_quick.py --title "Add CI pipeline" --repo PHOTO_AUTO_TAGGING --template infrastructure

# With dependency
python3 scripts/task_quick.py --title "Add search UI" --repo AIM_SOLO_ANALYSIS --depends-on CENTRAL-OPS-42

# Override any field while keeping the template for the rest
python3 scripts/task_quick.py --title "Add dark mode" --repo PHOTO_AUTO_TAGGING \
  --scope "Only update the CSS theme layer. Do not touch layout."
```

## All Flags

| Flag | Required | Description |
|---|---|---|
| `--title` | yes | Task title |
| `--repo` | yes | Target repo ID or alias |
| `--template` | no | `feature` (default), `bugfix`, `refactor`, `infrastructure` |
| `--priority` | no | Override priority (0–100) |
| `--task-type` | no | Override task_type string |
| `--objective` | no | Override objective section |
| `--context` | no | Override context section |
| `--scope` | no | Override scope section |
| `--deliverables` | no | Override deliverables section |
| `--acceptance` | no | Override acceptance criteria |
| `--testing` | no | Override testing section |
| `--reconciliation` | no | Override reconciliation section |
| `--depends-on` | no | Dependency task ID (repeatable) |
| `--list-templates` | no | Print template details and exit |

## AI Planner Usage

When creating tasks as a planner AI, prefer `task_quick.py` over the raw `planner-new | task-create` pipeline:

```python
# Instead of this multi-flag planner-new invocation:
python3 scripts/central_task_db.py planner-new \
  --title "..." --repo X --objective "..." --context "..." \
  --scope "..." --deliverables "..." --acceptance "..." \
  --testing "..." --dispatch "..." --closeout "..." \
  --reconciliation "..." --json | \
python3 scripts/central_task_db.py task-create --input - --json

# Use this:
python3 scripts/task_quick.py --title "..." --repo X --template bugfix
```

Only override individual sections when the task genuinely diverges from the template — most tasks don't need more than `--title`, `--repo`, and `--template`.

## Output

```
Created CENTRAL-OPS-62: Add export API endpoint
  template:  feature
  repo:      AIM_SOLO_ANALYSIS
  priority:  50
  dispatch:  repo=AIM_SOLO_ANALYSIS do task CENTRAL-OPS-62
```

The dispatch line is ready to use directly as a planner dispatch message.

## Underlying Contract

`task_quick.py` is a thin wrapper: it calls `planner-new` to generate the scaffold, then pipes the JSON to `task-create`. The full `planner-new` contract (task ID allocation via monotonic high watermark, `CENTRAL-OPS` series, execution defaults) is preserved underneath. Tasks created this way are indistinguishable from tasks created via the raw pipeline.
