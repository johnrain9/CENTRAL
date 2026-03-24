# AI Metrics Guide

Read this before touching anything in the `metrics/` directory.

## What This Directory Is

`metrics/` is the canonical query and analytics layer for the CENTRAL task system. All SQL that answers "how are workers performing?" lives here. Scripts and the planner UI call this library — they do not write their own SQL.

## Files

| File | Purpose |
|------|---------|
| `query.py` | Read-only SQL queries against the task DB. One function per metric. Import and call these — do not write new SQL elsewhere. |
| `worker_results.py` | Parses worker result JSON files from `state/central_runtime/.worker-results/`. Extracts quality signals: discovery density, files changed, audit verdicts, validation pass rates. |
| `METRICS_CATALOG.md` | Full catalog of derivable metrics by theme, with data source annotations and known gaps. Check here before adding a new metric — it may already be defined. |

## How to Add a New Query

Add it to `query.py`. Follow the existing pattern:

```python
def my_new_metric(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One-line description of what this returns.

    Returns list of dicts with keys: ...
    """
    sql = """..."""
    return _rows(conn, sql)
```

Rules:
- Read-only. No writes, no schema changes.
- Return plain `list[dict]` — no dataclasses, no custom types.
- Use `_rows(conn, sql, params)` for all queries (handles row_factory correctly).
- Use `_duration_stats(values)` for any duration aggregation — it gives median/IQR, not mean.
- Use `rework_count` from `metadata_json` for quality metrics, not `runtime_status` or retry counts. A task that failed runtime and was requeued is not a quality failure. A task that passed runtime but was rejected by the auditor is.
- Exclude `%-AUDIT` tasks from impl quality metrics (`WHERE t.task_id NOT LIKE '%-AUDIT'`).

## Quality Measurement — The Right Signal

**Use `rework_count` (in `tasks.metadata_json`), not `retry_count` (in `task_runtime_state`).**

- `retry_count` = runtime crashes/timeouts/quota hits. Operational noise.
- `rework_count` = audit rejections. This is the quality signal.

A task with `retry_count=3, rework_count=0` crashed 3 times but passed audit first try — it's a first-pass quality success.
A task with `retry_count=0, rework_count=2` ran cleanly but was rejected by the auditor twice — it's a quality failure.

## Consumers

| Consumer | How it uses this |
|----------|-----------------|
| `scripts/worker_analytics.py` | CLI renderer — calls `query.py` functions, formats as terminal tables. Add new queries here first, then wire the renderer. |
| `scripts/planner_ui.py` | Web dashboard — `/api/metrics/all` endpoint imports from `query.py` and `worker_results.py`. New dashboard sections need both a query function and a JS renderer. |

## Known Data Quality Issues

- **Claude/Sonnet richness data is invalid.** Claude Code workers output prose, not JSON. The `decisions`, `warnings`, `discoveries` arrays in their result files are empty. Only Codex/gpt-* and Grok richness data is reliable.
- **`tokens_cost_usd` is NULL for most historical tasks.** Cost tracking was added mid-project. Only recent tasks have cost data.
- **No time-series filter support in `query.py` yet.** All functions return all-time data. Post-filter in Python if needed, or add a `since` / `weeks` parameter.

## DB Location

```python
# Default — used by scripts
state/central_tasks.db

# How to open read-only (matches what query.py expects)
conn = sqlite3.connect("file:state/central_tasks.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
```

The key tables:
- `tasks` — task metadata, `planner_status`, `metadata_json` (has `rework_count`)
- `task_runtime_state` — runtime status, `effective_worker_model`, `started_at`, `finished_at`, `retry_count`, `tokens_cost_usd`
- `task_execution_settings` — `execution_metadata_json` (has `worker_effort`, `worker_backend`, `worker_model`)
- `task_events` — audit events (`planner.audit_accepted`, `planner.task_auto_rework`, etc.)
