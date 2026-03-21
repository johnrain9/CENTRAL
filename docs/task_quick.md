# task_quick — Streamlined Task Creation

`scripts/task_quick.py` is a planner-facing wrapper around `planner-new` + `task-create` that reduces task creation to 2 required flags.
It now runs `task-preflight` automatically, injects the preflight payload, and only then calls `task-create`.
In planner-ops smoke mode, the command runs the same pipeline against a temporary copied DB, prints that temp DB path for traceability, and removes the copied DB before exit.
If `--initiative` is omitted, `task_quick.py` defaults it to `one-off` so the 2-flag path still works even though initiative is now required at the DB layer.

## Minimum Usage

```sh
python3 scripts/task_quick.py --title "Fix login bug" --repo MOTO_HELPER
```

That's it. The tool picks the `feature` template by default, allocates the next `CENTRAL-OPS` task ID, fills required fields from the template, defaults initiative to `one-off`, and persists the task to the DB.
Use `--dry-run` for a safe validation path that runs preflight and prints planned task metadata without writing.
For planner-ops-specific smoke output, use `--planner-ops-smoke`.
That mode writes only to a temporary DB copy, synthesizes a temporary smoke-only title suffix so repeated validation runs do not collide with prior planner-ops tasks, and reports the cleanup explicitly in output.

## Templates

| Template | task_type | Default Priority | Use When |
|---|---|---|---|
| `feature` (default) | feature | 50 | New capability or behavior |
| `bugfix` | bugfix | 70 | Diagnosed or reported defect |
| `refactor` | refactor | 40 | Code quality improvement, no behavior change |
| `infrastructure` | infrastructure | 60 | Tooling, CI, config, or platform work |
| `design` | design | 30 | Architecture or design brief; output is a doc, not code |
| `docs` | docs | 35 | Create or update documentation |
| `repo-health` | repo-health | 55 | Health adapter, repo onboarding, or health integration |
| `validation` | validation | 65 | End-to-end acceptance test or smoke-test run |
| `cleanup` | cleanup | 45 | Remove dead code, deprecated layers, or unused artifacts |
| `planner-ops` | planner-ops | 50 | CENTRAL planner tooling, workflow scripts, dispatch infra |

```sh
python3 scripts/task_quick.py --list-templates   # show all templates with details
```

## When to Use Each Template

**feature** — Adding new behavior that does not exist yet. Worker produces running code and tests.

**bugfix** — A specific defect has been identified. Worker diagnoses root cause, fixes minimally, adds regression test.

**refactor** — Code quality work with no external behavior change. Worker must run tests before and after.

**infrastructure** — CI, config, tooling, or platform changes. Worker validates end-to-end in the real environment.

**design** — Output is a doc or decision record, not code. Use before implementation tasks when the approach is not yet settled. Worker proposes follow-on implementation tasks.

**docs** — README, reference docs, or guides. Worker writes or updates docs without touching implementation code.

**repo-health** — Implementing the CENTRAL repo health adapter contract for a target repo. Worker validates adapter returns valid status and repo is registered.

**validation** — Running acceptance criteria in a real environment and documenting results. Worker does not implement fixes — files follow-on tasks for failures.

**cleanup** — Removing dead code, deprecated APIs, or unused layers. Worker verifies nothing in use is removed.

**planner-ops** — Changes to CENTRAL planner tooling (`task_quick.py`, `planner-new`, dispatcher workflows, etc.). Worker must smoke-test the changed tooling end-to-end.

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

# Design brief (produces a doc, not code)
python3 scripts/task_quick.py --title "Design auth overhaul" --repo CENTRAL --template design

# Documentation task
python3 scripts/task_quick.py --title "Write README for voice_transcription" --repo VOICE_TRANSCRIPTION --template docs

# Repo health adapter
python3 scripts/task_quick.py --title "Add health adapter" --repo PHOTO_AUTO_TAGGING --template repo-health

# End-to-end validation run
python3 scripts/task_quick.py --title "Validate voice PTT on real desktop" --repo CENTRAL --template validation

# Planner preflight validation without persistence
python3 scripts/task_quick.py --title "Verify preflight integration" --repo CENTRAL --template planner-ops --dry-run
python3 scripts/task_quick.py --title "Verify planner preflight smoke" --repo CENTRAL --template planner-ops --planner-ops-smoke

Expected smoke output pattern:
```
Planner-ops preflight smoke: pass
  task_id:      CENTRAL-OPS-999
  template:     planner-ops
  repo:         CENTRAL
  series:       CENTRAL-OPS
  priority:     50
  preflight:    strong_overlap (token: abc...)
  alpha:        alpha-1234abcd56
  created_id:   CENTRAL-OPS-999
  created_state:todo
  created_ver:  v1
  smoke_db:     /tmp/.../central_tasks_smoke.db
  cleanup:      temp smoke DB removed after validation
```

In smoke mode, `task_quick.py` adds a temporary smoke-only marker suffix to the scaffold title before preflight and `task-create` run against the copied DB. That keeps the user-facing command stable while avoiding exact-duplicate blockers when earlier smoke tasks already exist in the source DB.

# Remove deprecated layer
python3 scripts/task_quick.py --title "Remove .worker-reports layer" --repo CENTRAL --template cleanup

# Planner tooling change
python3 scripts/task_quick.py --title "Add planner macro tool" --repo CENTRAL --template planner-ops

# With initiative tag (groups work by feature area in view-summary)
python3 scripts/task_quick.py --title "Add runtime heartbeat tuning" --repo CENTRAL \
  --template infrastructure --initiative dispatcher-infrastructure

# With dependency
python3 scripts/task_quick.py --title "Add search UI" --repo AIM_SOLO_ANALYSIS --depends-on CENTRAL-OPS-42

# Override any field while keeping the template for the rest
python3 scripts/task_quick.py --title "Add dark mode" --repo PHOTO_AUTO_TAGGING \
  --scope "Only update the CSS theme layer. Do not touch layout."

# Different task ID series
python3 scripts/task_quick.py --title "Debug transaction nesting" --repo PHOTO_AUTO_TAGGING \
  --template bugfix --series AUT-OPS
```

## All Flags

| Flag | Required | Description |
|---|---|---|
| `--title` | yes | Task title |
| `--repo` | yes | Target repo ID or alias |
| `--db-path` | no | Override the CENTRAL DB path (useful for isolated smoke tests) |
| `--template` | no | Template name (default: `feature`) |
| `--series` | no | Task ID series (default: `CENTRAL-OPS`) |
| `--priority` | no | Override priority (0–100) |
| `--task-type` | no | Override task_type string |
| `--objective` | no | Override objective section |
| `--context` | no | Override context section |
| `--scope` | no | Override scope section |
| `--deliverables` | no | Override deliverables section |
| `--acceptance` | no | Override acceptance criteria |
| `--testing` | no | Override testing section |
| `--reconciliation` | no | Override reconciliation section |
| `--dry-run` | no | Run preflight/validation and skip DB write |
| `--planner-ops-smoke` | no | Planner-ops preflight smoke mode: runs planner-new, task-preflight, and task-create against a temporary DB copy; exits non-zero on preflight validation mismatch |
| `--depends-on` | no | Dependency task ID (repeatable) |
| `--initiative` | no | Initiative/epic tag for grouping (e.g. `dispatcher-infrastructure`) |
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
  series:    CENTRAL-OPS
  priority:  50
  dispatch:  repo=AIM_SOLO_ANALYSIS do task CENTRAL-OPS-62
```

If `--initiative` was supplied, it appears on its own line:

```
Created CENTRAL-OPS-63: Add runtime heartbeat tuning
  template:  infrastructure
  repo:      CENTRAL
  series:    CENTRAL-OPS
  priority:  60
  initiative: dispatcher-infrastructure
  dispatch:  repo=CENTRAL do task CENTRAL-OPS-63
```

The dispatch line is ready to use directly as a planner dispatch message.

## Underlying Contract

`task_quick.py` is a thin wrapper: it calls `planner-new` to generate the scaffold, then pipes the JSON to `task-create`. The full `planner-new` contract (task ID allocation via monotonic high watermark, execution defaults) is preserved underneath. Tasks created this way are indistinguishable from tasks created via the raw pipeline.
