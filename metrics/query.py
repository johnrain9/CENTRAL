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


def _duration_stats(values: list[float]) -> dict[str, float | int | None]:
    """Return robust duration statistics for a list of second values.

    Uses median and IQR (P25–P75) as the primary summary — not mean — so that
    timeout-ceiling hits and random hangs don't distort the picture.

    Outlier fence: values above P75 + 3×IQR are flagged and excluded from the
    'clean' stats (they are kept in raw percentiles).  This is a loose fence
    (3× rather than the standard 1.5×) so genuine long tasks are preserved.

    Returns:
        n               — sample size (all values)
        n_outliers      — values above the outlier fence
        p25, p50, p75   — quartiles (all values, including outliers)
        iqr             — P75 - P25
        p90, p99        — upper tail (all values)
        median_clean    — P50 after removing outliers (None if none removed)
    """
    if not values:
        return {
            "n": 0, "n_outliers": 0,
            "p25": None, "p50": None, "p75": None, "iqr": None,
            "p90": None, "p99": None, "median_clean": None,
        }
    p25 = _percentile(values, 25)
    p50 = _percentile(values, 50)
    p75 = _percentile(values, 75)
    p90 = _percentile(values, 90)
    p99 = _percentile(values, 99)
    iqr = (p75 - p25) if (p75 is not None and p25 is not None) else None
    fence = (p75 + 3 * iqr) if iqr is not None else None

    outliers = [v for v in values if fence is not None and v > fence]
    clean = [v for v in values if fence is None or v <= fence]
    median_clean = _percentile(clean, 50) if outliers else None

    return {
        "n": len(values),
        "n_outliers": len(outliers),
        "p25": round(p25, 1) if p25 is not None else None,
        "p50": round(p50, 1) if p50 is not None else None,
        "p75": round(p75, 1) if p75 is not None else None,
        "iqr": round(iqr, 1) if iqr is not None else None,
        "p90": round(p90, 1) if p90 is not None else None,
        "p99": round(p99, 1) if p99 is not None else None,
        "median_clean": round(median_clean, 1) if median_clean is not None else None,
    }


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
    # Join impl tasks (non-AUDIT) with runtime state and metadata.
    # Include both 'done' and 'timeout' so we can count timeouts separately,
    # but only 'done' tasks count toward quality and clean duration stats.
    sql = """
        SELECT
            trs.effective_worker_model,
            trs.runtime_status,
            t.metadata_json,
            trs.started_at,
            trs.finished_at
        FROM tasks t
        JOIN task_runtime_state trs ON trs.task_id = t.task_id
        WHERE trs.runtime_status IN ('done', 'timeout')
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
        done_rows = [r for r in rows if r["runtime_status"] == "done"]
        timeout_count = sum(1 for r in rows if r["runtime_status"] == "timeout")
        total = len(done_rows)

        rework_counts: list[int] = []
        for r in done_rows:
            try:
                meta = json.loads(r["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            rework_counts.append(int(meta.get("rework_count") or 0))
        tasks_reworked = sum(1 for rc in rework_counts if rc > 0)

        # Duration from 'done' tasks only — timeouts hit the ceiling and are excluded
        durations = [
            d for d in (
                _duration_seconds(r["started_at"], r["finished_at"]) for r in done_rows
            ) if d is not None and d >= 0
        ]
        dur = _duration_stats(durations)

        result.append({
            "effective_worker_model": model,
            "total_done": total,
            "timeout_count": timeout_count,
            "tasks_reworked": tasks_reworked,
            "rework_rate": round(tasks_reworked / total, 4) if total else None,
            "first_pass_rate": round((total - tasks_reworked) / total, 4) if total else None,
            "avg_rework_cycles": round(sum(rework_counts) / len(rework_counts), 3) if rework_counts else None,
            "max_rework_cycles": max(rework_counts) if rework_counts else None,
            # Duration: median + IQR; timeouts excluded; outlier count surfaced
            "duration_p50_s": dur["p50"],
            "duration_p25_s": dur["p25"],
            "duration_p75_s": dur["p75"],
            "duration_iqr_s": dur["iqr"],
            "duration_p90_s": dur["p90"],
            "duration_n_outliers": dur["n_outliers"],
            "duration_median_clean_s": dur["median_clean"],
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
    """Work duration distribution by effective_worker_model.

    Duration is measured as finished_at − started_at (seconds).
    Only 'done' tasks are included — 'timeout' tasks hit the ceiling value by
    definition and would inflate upper percentiles unfairly.

    Reports median + IQR (P25/P50/P75) as the primary summary, plus P90/P99
    for the upper tail, plus an outlier count (values above P75 + 3×IQR).

    Returns list of dicts with keys:
        effective_worker_model, n, n_outliers,
        p25_s, p50_s, p75_s, iqr_s, p90_s, p99_s, median_clean_s.
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
        s = _duration_stats(durations)
        result.append({
            "effective_worker_model": model,
            "n": s["n"],
            "n_outliers": s["n_outliers"],
            "p25_s": s["p25"],
            "p50_s": s["p50"],
            "p75_s": s["p75"],
            "iqr_s": s["iqr"],
            "p90_s": s["p90"],
            "p99_s": s["p99"],
            "median_clean_s": s["median_clean"],
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
    # Work time excludes 'timeout' — those hit the ceiling and aren't real durations.
    # Lead and cycle times include all terminal statuses since they measure
    # calendar time (queueing + end-to-end), not execution quality.
    sql = """
        SELECT
            t.created_at,
            t.closed_at,
            trs.claimed_at,
            trs.started_at,
            trs.finished_at,
            trs.runtime_status
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
        ct = _duration_seconds(r["created_at"], r["closed_at"])
        if lt is not None and lt >= 0:
            lead_times.append(lt)
        if ct is not None and ct >= 0:
            cycle_times.append(ct)
        # Work time: only 'done' — timeouts hit the ceiling
        if r["runtime_status"] == "done":
            wt = _duration_seconds(r["started_at"], r["finished_at"])
            if wt is not None and wt >= 0:
                work_times.append(wt)

    ls = _duration_stats(lead_times)
    ws = _duration_stats(work_times)
    cs = _duration_stats(cycle_times)

    def _flatten(prefix: str, s: dict) -> dict:
        return {
            f"{prefix}_n": s["n"],
            f"{prefix}_n_outliers": s["n_outliers"],
            f"{prefix}_p25_s": s["p25"],
            f"{prefix}_p50_s": s["p50"],
            f"{prefix}_p75_s": s["p75"],
            f"{prefix}_iqr_s": s["iqr"],
            f"{prefix}_p90_s": s["p90"],
            f"{prefix}_median_clean_s": s["median_clean"],
        }

    return [{
        **_flatten("lead_time", ls),
        **_flatten("work_time", ws),
        **_flatten("cycle_time", cs),
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
