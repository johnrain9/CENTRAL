# Worker Model Policy

The dispatcher selects worker models through a three-level priority chain:

```
task_override  >  policy_default (task class)  >  dispatcher_default
```

## Priority Chain

| Level | Source tag | How to set |
|-------|-----------|------------|
| Task override | `task_override` | `execution.metadata.claude_model` or `codex_model` on the task |
| Policy default | `policy_default` | Automatic — derived from task class (see below) |
| Dispatcher default | `dispatcher_default` | `dispatcher_control.py config --default-worker-model` or env vars |

## Task Classes

Tasks are classified as **design** or **routine**:

| Signal | Triggers `design` class |
|--------|------------------------|
| `execution.metadata.task_class` | `"design"` |
| `metadata.tags` | any of: `design`, `architecture`, `planning`, `spec` |
| `metadata.phase` | contains: `design`, `architecture`, `planning`, `spec` |
| (all other tasks) | `routine` |

## Model Tiers

| Task class | Claude backend | Codex backend |
|------------|---------------|---------------|
| `design` (high tier) | `claude-opus-4-6` | `o3` |
| `routine` (medium tier) | `claude-sonnet-4-6` | `gpt-5-codex` |

Medium-tier constants are also the dispatcher-level defaults, so routine tasks fall through to the operator-configured default when no policy override applies.

## Environment Variable Overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `CENTRAL_DISPATCHER_HIGH_TIER_CLAUDE_MODEL` | `claude-opus-4-6` | High-tier Claude model |
| `CENTRAL_DISPATCHER_HIGH_TIER_CODEX_MODEL` | `o3` | High-tier Codex model |
| `CENTRAL_DISPATCHER_MEDIUM_TIER_CLAUDE_MODEL` | `claude-sonnet-4-6` | Medium-tier Claude model |
| `CENTRAL_DISPATCHER_MEDIUM_TIER_CODEX_MODEL` | `gpt-5-codex` | Medium-tier Codex model |

## When to Use Each Tier

**High tier (design tasks):** architecture decisions, multi-repo design, spec writing, planning tasks where reasoning quality matters more than throughput. Tag tasks with `design`, `architecture`, `planning`, or `spec` or set `metadata.phase` accordingly.

**Medium tier (routine tasks):** implementation, bug fixes, refactoring, migrations, test writing, documentation. These are the default and cover most worker throughput.

**Explicit task override:** exceptional cases where a specific task needs a model outside the policy (e.g., a routine task requiring extended context, or a design task on a time-sensitive path). Set `execution.metadata.claude_model` or `execution.metadata.codex_model` directly on the task record.

## Inspecting Model Selection

Model selection is recorded in three places after a worker runs:

### 1. DB runtime state (primary, queryable after the run)

The `task_runtime_state` table now stores `effective_worker_model` and `worker_model_source`
for every task that has been dispatched. Read them via `task-show`:

```bash
python3 scripts/central_task_db.py task-show --task-id <TASK-ID> --json | \
  python3 -c "import json,sys; r=json.load(sys.stdin); rt=r.get('runtime') or {}; print(rt.get('effective_worker_model'), rt.get('worker_model_source'))"
```

Or read the human-readable task card (model line appears under Runtime Status when set):

```bash
python3 scripts/central_task_db.py view-task-card --task-id <TASK-ID>
```

### 2. Assignments view

`view-assignments` now includes `effective_worker_model` and `worker_model_source` columns:

```bash
python3 scripts/central_task_db.py view-assignments --json
```

### 3. Dispatcher logs and runtime event notes

Every `worker_spawned` log line includes:

```
model=<effective_model>  model_source=<source>
```

The runtime event recorded on the `running` transition also carries
`model=<effective_model> model_source=<source>` in its notes field.

### Source values

```
worker_model_source=task_override      # explicit per-task override won
worker_model_source=policy_default     # task-class policy selected the model
worker_model_source=dispatcher_default # fell through to operator-configured default
```

### Writing model info manually (operator/debugging)

If you need to backfill or override the stored model for a task:

```bash
python3 scripts/central_task_db.py runtime-transition \
  --task-id <TASK-ID> \
  --status running \
  --effective-worker-model claude-sonnet-4-6 \
  --worker-model-source policy_default \
  --actor-id operator
```
