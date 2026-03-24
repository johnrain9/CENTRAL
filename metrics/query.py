#!/usr/bin/env python3
"""Composable read-only SQL queries against the CENTRAL task database.

Each public function accepts a sqlite3.Connection and optional filter
parameters, executes a query, and returns a list of plain dicts suitable
for downstream rendering or JSON serialisation.

All queries are read-only; no writes are performed.

Typical usage::

    import sqlite3
    from metrics.query import model_scorecard

    conn = sqlite3.connect("state/central_tasks.db")
    conn.row_factory = sqlite3.Row
    rows = model_scorecard(conn)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute *sql* and return results as a list of plain dicts."""
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _percentile(values: list[float], pct: float) -> float | None:
    """Return the *pct*-th percentile (0–100) of *values*, or None if empty."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    """Return seconds between two ISO-8601 timestamps, or None if either is missing."""
    if not start or not end:
        return None
    from datetime import datetime, timezone

    def _parse(ts: str) -> datetime:
        ts = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            # Drop sub-second precision if present
            return datetime.fromisoformat(ts[:19] + "+00:00")

    try:
        delta = _parse(end) - _parse(start)
        return delta.total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Model scorecard
# ---------------------------------------------------------------------------

def model_scorecard(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Quality scorecard per effective_worker_model, based on audit outcomes.

    Quality is measured by rework_count (audit rejections per task), not by
    runtime_status — tasks are almost never allowed to stay 'failed' since
    they are requeued on failure.  A task with rework_count=0 (or absent)
    passed its audit on the first submission.

    Excludes AUDIT tasks themselves (they are the judges, not the judged).

    Returns one dict per model with keys:
        effective_worker_model, total_done, tasks_reworked, rework_rate,
        first_pass_rate, avg_rework_cycles, max_rework_cycles,
        avg_duration_seconds
    """
    # Join impl tasks (non-AUDIT) with runtime state and metadata
    sql = """
        SELECT
            trs.effective_worker_model,
            t.metadata_json,
            trs.started_at,
            trs.finished_at
        FROM tasks t
        JOIN task_runtime_state trs ON trs.task_id = t.task_id
        WHERE trs.runtime_status = 'done'
          AND t.task_id NOT LIKE '%-AUDIT'
    """
    raw = _rows(conn, sql)

    from collections import defaultdict
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        model = r["effective_worker_model"] or "unknown"
        buckets[model].append(r)

    result = []
    for model, rows in sorted(buckets.items()):
        total = len(rows)
        rework_counts: list[int] = []
        for r in rows:
            try:
                meta = json.loads(r["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            rework_counts.append(int(meta.get("rework_count") or 0))
        tasks_reworked = sum(1 for rc in rework_counts if rc > 0)
        durations = [
            d for d in (
                _duration_seconds(r["started_at"], r["finished_at"]) for r in rows
            ) if d is not None
        ]
        result.append({
            "effective_worker_model": model,
            "total_done": total,
            "tasks_reworked": tasks_reworked,
            "rework_rate": round(tasks_reworked / total, 4) if total else None,
            "first_pass_rate": round((total - tasks_reworked) / total, 4) if total else None,
            "avg_rework_cycles": round(sum(rework_counts) / len(rework_counts), 3) if rework_counts else None,
            "max_rework_cycles": max(rework_counts) if rework_counts else None,
            "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        })
    return result


# ---------------------------------------------------------------------------
# 2. First-pass rates by dimension (audit-based quality, not runtime status)
# ---------------------------------------------------------------------------

def _rework_rates_by_dim(conn: sqlite3.Connection, dim_col: str, dim_expr: str) -> list[dict[str, Any]]:
    """Shared helper: rework rates for impl tasks grouped by a task dimension.

    'First-pass' means the task completed without any audit rejection
    (rework_count absent or 0 in metadata_json).  AUDIT tasks are excluded
    since they are the judges, not the judged.
    """
    sql = f"""
        SELECT
            {dim_expr} AS dim,
            t.metadata_json
        FROM tasks t
        JOIN task_runtime_state trs ON trs.task_id = t.task_id
        WHERE trs.runtime_status = 'done'
          AND t.task_id NOT LIKE '%-AUDIT'
    """
    raw = _rows(conn, sql)

    from collections import defaultdict
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in raw:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        dim = str(r["dim"] or "unset")
        buckets[dim].append(int(meta.get("rework_count") or 0))

    result = []
    for dim, rework_counts in sorted(buckets.items()):
        total = len(rework_counts)
        reworked = sum(1 for rc in rework_counts if rc > 0)
        result.append({
            dim_col: dim,
            "total_done": total,
            "tasks_reworked": reworked,
            "first_pass_rate": round((total - reworked) / total, 4) if total else None,
            "rework_rate": round(reworked / total, 4) if total else None,
            "avg_rework_cycles": round(sum(rework_counts) / total, 3) if total else None,
        })
    return result


def first_pass_rates_by_task_type(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """First-pass audit rate grouped by task_type (impl tasks only)."""
    return _rework_rates_by_dim(conn, "task_type", "t.task_type")


def first_pass_rates_by_repo(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """First-pass audit rate grouped by target_repo_id (impl tasks only)."""
    return _rework_rates_by_dim(conn, "repo", "t.target_repo_id")


def first_pass_rates_by_initiative(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """First-pass audit rate grouped by initiative (impl tasks only)."""
    return _rework_rates_by_dim(conn, "initiative", "COALESCE(t.initiative, 'unset')")


# ---------------------------------------------------------------------------
# 3. Throughput over time
# ---------------------------------------------------------------------------

def throughput_daily(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Daily task completions for the last *days* days, grouped by repo.

    Returns list of dicts with keys: date, target_repo_id, completed.
    """
    sql = """
        SELECT
            DATE(trs.finished_at) AS date,
            t.target_repo_id,
            COUNT(*) AS completed
        FROM task_runtime_state trs
        JOIN tasks t ON t.task_id = trs.task_id
        WHERE trs.runtime_status = 'done'
          AND trs.finished_at >= DATE('now', ?)
        GROUP BY DATE(trs.finished_at), t.target_repo_id
        ORDER BY date DESC, t.target_repo_id
    """
    return _rows(conn, sql, (f"-{days} days",))


def throughput_weekly(
    conn: sqlite3.Connection,
    *,
    weeks: int = 12,
) -> list[dict[str, Any]]:
    """Weekly task completions for the last *weeks* weeks, grouped by repo.

    Returns list of dicts with keys: week_start, target_repo_id, completed.
    Week start is the Monday of the ISO week (strftime %Y-%W).
    """
    sql = """
        SELECT
            STRFTIME('%Y-%W', trs.finished_at) AS week,
            t.target_repo_id,
            COUNT(*) AS completed
        FROM task_runtime_state trs
        JOIN tasks t ON t.task_id = trs.task_id
        WHERE trs.runtime_status = 'done'
          AND trs.finished_at >= DATE('now', ?)
        GROUP BY STRFTIME('%Y-%W', trs.finished_at), t.target_repo_id
        ORDER BY week DESC, t.target_repo_id
    """
    return _rows(conn, sql, (f"-{weeks * 7} days",))


# ---------------------------------------------------------------------------
# 4. Retry distribution and recovery rate
# ---------------------------------------------------------------------------

def retry_distribution(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Histogram of retry_count at terminal state.

    Returns list of dicts with keys: retry_count, tasks, pct_of_total.
    Sorted ascending by retry_count.
    """
    sql = """
        SELECT
            retry_count,
            COUNT(*) AS tasks
        FROM task_runtime_state
        WHERE runtime_status IN ('done', 'failed', 'timeout', 'canceled')
        GROUP BY retry_count
        ORDER BY retry_count
    """
    rows = _rows(conn, sql)
    total = sum(r["tasks"] for r in rows)
    for r in rows:
        r["pct_of_total"] = round(r["tasks"] / total, 4) if total else None
    return rows


def retry_recovery_rate(conn: sqlite3.Connection) -> dict[str, Any]:
    """Of tasks that were retried (retry_count > 0), what fraction eventually succeeded?

    Returns a single dict with keys:
        retried_total, retried_done, recovery_rate,
        max_retry_exhausted (tasks that hit max retries and still failed/timed-out).
    """
    sql = """
        SELECT
            COUNT(*) AS retried_total,
            SUM(CASE WHEN runtime_status = 'done' THEN 1 ELSE 0 END) AS retried_done,
            MAX(retry_count) AS max_retry_seen
        FROM task_runtime_state
        WHERE retry_count > 0
          AND runtime_status IN ('done', 'failed', 'timeout', 'canceled')
    """
    rows = _rows(conn, sql)
    row = rows[0] if rows else {}
    total = row.get("retried_total") or 0
    done = row.get("retried_done") or 0

    # Tasks whose retry_count equals the observed max and are still terminal failures
    max_retry = row.get("max_retry_seen") or 0
    exhausted_sql = """
        SELECT COUNT(*) AS cnt
        FROM task_runtime_state
        WHERE retry_count = ?
          AND runtime_status IN ('failed', 'timeout', 'canceled')
    """
    exhausted_rows = _rows(conn, exhausted_sql, (max_retry,))
    exhausted = exhausted_rows[0]["cnt"] if exhausted_rows else 0

    return {
        "retried_total": total,
        "retried_done": done,
        "recovery_rate": round(done / total, 4) if total else None,
        "max_retry_seen": max_retry,
        "max_retry_exhausted": exhausted,
    }


def retry_heatmap(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Model × task_type retry frequency heatmap.

    Returns list of dicts with keys:
        effective_worker_model, task_type, total, avg_retries, max_retries.
    Sorted descending by avg_retries.
    """
    sql = """
        SELECT
            trs.effective_worker_model,
            t.task_type,
            COUNT(*) AS total,
            CAST(SUM(trs.retry_count) AS REAL) / COUNT(*) AS avg_retries,
            MAX(trs.retry_count) AS max_retries
        FROM task_runtime_state trs
        JOIN tasks t ON t.task_id = trs.task_id
        WHERE trs.runtime_status IN ('done', 'failed', 'timeout', 'canceled')
        GROUP BY trs.effective_worker_model, t.task_type
        ORDER BY avg_retries DESC
    """
    rows = _rows(conn, sql)
    for r in rows:
        if r["avg_retries"] is not None:
            r["avg_retries"] = round(r["avg_retries"], 3)
    return rows


# ---------------------------------------------------------------------------
# 5. Duration stats (P50/P90/P99 by model)
# ---------------------------------------------------------------------------

def duration_percentiles_by_model(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Work duration P50/P90/P99 by effective_worker_model.

    Duration is measured as finished_at − started_at (seconds).
    Only 'done' tasks are included (excludes failures which may be truncated).

    Returns list of dicts with keys:
        effective_worker_model, sample_size, p50_seconds, p90_seconds, p99_seconds.
    """
    sql = """
        SELECT
            effective_worker_model,
            started_at,
            finished_at
        FROM task_runtime_state
        WHERE runtime_status = 'done'
          AND started_at IS NOT NULL
          AND finished_at IS NOT NULL
    """
    raw = _rows(conn, sql)

    from collections import defaultdict
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in raw:
        model = r["effective_worker_model"] or "unknown"
        d = _duration_seconds(r["started_at"], r["finished_at"])
        if d is not None and d >= 0:
            buckets[model].append(d)

    result = []
    for model, durations in sorted(buckets.items()):
        result.append({
            "effective_worker_model": model,
            "sample_size": len(durations),
            "p50_seconds": _percentile(durations, 50),
            "p90_seconds": _percentile(durations, 90),
            "p99_seconds": _percentile(durations, 99),
        })
    return result


# ---------------------------------------------------------------------------
# 6. Effort calibration cross-tab
# ---------------------------------------------------------------------------

def effort_calibration_crosstab(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Cross-tab of worker_effort × effective_worker_model vs audit quality.

    'Quality' is measured by rework_count, not runtime_status.  A task that
    ran successfully but was sent back by the auditor is NOT a first-pass success.
    AUDIT tasks are excluded (they have no rework_count).

    worker_effort is stored in task_execution_settings.execution_metadata_json.

    Returns list of dicts with keys:
        worker_effort, effective_worker_model, total_done,
        tasks_reworked, first_pass_rate, rework_rate, avg_rework_cycles.
    """
    sql = """
        SELECT
            tes.execution_metadata_json,
            trs.effective_worker_model,
            t.metadata_json
        FROM task_runtime_state trs
        JOIN task_execution_settings tes ON tes.task_id = trs.task_id
        JOIN tasks t ON t.task_id = trs.task_id
        WHERE trs.runtime_status = 'done'
          AND t.task_id NOT LIKE '%-AUDIT'
    """
    raw = _rows(conn, sql)

    from collections import defaultdict
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in raw:
        try:
            exec_meta = json.loads(r["execution_metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            exec_meta = {}
        try:
            task_meta = json.loads(r["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            task_meta = {}
        effort = exec_meta.get("worker_effort") or "unset"
        model = r["effective_worker_model"] or "unknown"
        rework_count = int(task_meta.get("rework_count") or 0)
        buckets[(effort, model)].append(rework_count)

    result = []
    for (effort, model), rework_counts in sorted(buckets.items()):
        total = len(rework_counts)
        reworked = sum(1 for rc in rework_counts if rc > 0)
        result.append({
            "worker_effort": effort,
            "effective_worker_model": model,
            "total_done": total,
            "tasks_reworked": reworked,
            "first_pass_rate": round((total - reworked) / total, 4) if total else None,
            "rework_rate": round(reworked / total, 4) if total else None,
            "avg_rework_cycles": round(sum(rework_counts) / total, 3) if total else None,
        })
    return result


# ---------------------------------------------------------------------------
# 7. Lead / work / cycle time
# ---------------------------------------------------------------------------

def lead_work_cycle_times(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Lead, work, and cycle time statistics across all terminal tasks.

    Definitions:
        lead_time   = claimed_at  − tasks.created_at   (queue wait)
        work_time   = finished_at − started_at          (active execution)
        cycle_time  = tasks.closed_at − tasks.created_at (end-to-end)

    Returns a single summary dict with keys:
        sample_size,
        lead_time_{p50,p90,p99}_seconds,
        work_time_{p50,p90,p99}_seconds,
        cycle_time_{p50,p90,p99}_seconds.
    """
    sql = """
        SELECT
            t.created_at,
            t.closed_at,
            trs.claimed_at,
            trs.started_at,
            trs.finished_at
        FROM tasks t
        JOIN task_runtime_state trs ON t.task_id = trs.task_id
        WHERE trs.runtime_status IN ('done', 'failed', 'timeout', 'canceled')
    """
    raw = _rows(conn, sql)

    lead_times: list[float] = []
    work_times: list[float] = []
    cycle_times: list[float] = []

    for r in raw:
        lt = _duration_seconds(r["created_at"], r["claimed_at"])
        wt = _duration_seconds(r["started_at"], r["finished_at"])
        ct = _duration_seconds(r["created_at"], r["closed_at"])
        if lt is not None and lt >= 0:
            lead_times.append(lt)
        if wt is not None and wt >= 0:
            work_times.append(wt)
        if ct is not None and ct >= 0:
            cycle_times.append(ct)

    def _pcts(vals: list[float]) -> dict[str, float | None]:
        return {
            "p50": _percentile(vals, 50),
            "p90": _percentile(vals, 90),
            "p99": _percentile(vals, 99),
        }

    lp = _pcts(lead_times)
    wp = _pcts(work_times)
    cp = _pcts(cycle_times)

    return [{
        "sample_size": len(raw),
        "lead_time_p50_seconds": lp["p50"],
        "lead_time_p90_seconds": lp["p90"],
        "lead_time_p99_seconds": lp["p99"],
        "work_time_p50_seconds": wp["p50"],
        "work_time_p90_seconds": wp["p90"],
        "work_time_p99_seconds": wp["p99"],
        "cycle_time_p50_seconds": cp["p50"],
        "cycle_time_p90_seconds": cp["p90"],
        "cycle_time_p99_seconds": cp["p99"],
    }]


# ---------------------------------------------------------------------------
# 8. Failure mode grouping
# ---------------------------------------------------------------------------

def failure_mode_groups(
    conn: sqlite3.Connection,
    *,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Group process-level failures by normalised last_runtime_error prefix.

    NOTE: these are *operational* failures (crashes, quota exhaustion, timeouts,
    operator kills) — NOT audit rejections.  Tasks are almost never left in a
    terminal failed state; they are requeued.  Use model_scorecard() or
    first_pass_rates_by_*() for quality/audit-rejection metrics.

    Normalisation: lower-case, strip leading whitespace, truncate to 120 chars,
    then strip trailing punctuation/whitespace so minor variance collapses.

    Returns list of dicts (up to *top_n*) with keys:
        error_prefix, count, example_task_ids (up to 3).
    Sorted descending by count.
    """
    sql = """
        SELECT
            task_id,
            last_runtime_error
        FROM task_runtime_state
        WHERE runtime_status IN ('failed', 'timeout', 'canceled')
          AND last_runtime_error IS NOT NULL
          AND last_runtime_error != ''
    """
    raw = _rows(conn, sql)

    import re
    from collections import defaultdict

    def _normalise(err: str) -> str:
        err = err.strip().lower()[:120]
        err = re.sub(r"[\s,.:;!?]+$", "", err)
        # Collapse run-together digits/hashes that vary per-run
        err = re.sub(r"\b[0-9a-f]{8,}\b", "<hash>", err)
        err = re.sub(r"\b\d{4,}\b", "<n>", err)
        return err

    groups: dict[str, list[str]] = defaultdict(list)
    for r in raw:
        key = _normalise(r["last_runtime_error"])
        groups[key].append(r["task_id"])

    result = []
    for prefix, task_ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        result.append({
            "error_prefix": prefix,
            "count": len(task_ids),
            "example_task_ids": task_ids[:3],
        })
    return result[:top_n]
