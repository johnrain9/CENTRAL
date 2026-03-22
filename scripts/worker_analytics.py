#!/usr/bin/env python3
"""
worker_analytics.py — Model efficiency and worker performance analytics.

Reads the live CENTRAL DB. No log parsing, no external deps beyond stdlib + sqlite3.

Usage:
    python3 scripts/worker_analytics.py                    # last 7 days (default)
    python3 scripts/worker_analytics.py --all-time         # no time filter
    python3 scripts/worker_analytics.py --json             # machine-readable
    python3 scripts/worker_analytics.py --model gpt-5.3-codex
    python3 scripts/worker_analytics.py --repo ecosystem
    python3 scripts/worker_analytics.py --since 24h
    python3 scripts/worker_analytics.py --since 7d
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "state" / "central_tasks.db"

# Tasks with retry_count >= this are treated as "hit retry ceiling" and excluded from main stats.
# Set to match the historical max-retries config (was 6 in billing era, 3-4 currently).
MAX_RETRY_WALL = 5


def resolve_db(path: str | None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("CENTRAL_TASK_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB


def parse_since(value: str | None, *, all_time: bool = False) -> str | None:
    """Convert '24h', '7d', '2w' to an ISO timestamp cutoff.

    Defaults to 7d if neither --since nor --all-time is given.
    Returns None (no cutoff) when --all-time is set.
    """
    if all_time:
        return None
    if not value:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return cutoff.isoformat()
    m = re.match(r"^(\d+)([hdw])$", value.strip().lower())
    if not m:
        print(f"Invalid --since format: {value!r}. Use e.g. 24h, 7d, 2w.", file=sys.stderr)
        sys.exit(1)
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    cutoff = datetime.now(timezone.utc) - delta
    return cutoff.isoformat()


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Raw task fetch + outlier detection
# ---------------------------------------------------------------------------


def query_tasks_raw(
    conn: sqlite3.Connection,
    *,
    repo: str | None,
    model: str | None,
    since: str | None,
) -> list[dict]:
    """Fetch individual completed task rows for outlier detection and aggregation."""
    where_clauses = [
        "r.runtime_status = 'done'",
        "r.effective_worker_model IS NOT NULL",
        "r.claimed_at IS NOT NULL",
        "r.finished_at IS NOT NULL",
    ]
    params: list = []

    if repo:
        where_clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        where_clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        where_clauses.append("r.finished_at >= ?")
        params.append(since)

    where = " AND ".join(where_clauses)
    sql = f"""
    SELECT
        r.task_id,
        t.title,
        t.target_repo_id AS repo,
        r.effective_worker_model AS model,
        r.retry_count,
        t.task_type,
        ROUND((JULIANDAY(r.finished_at) - JULIANDAY(r.claimed_at)) * 1440, 1) AS duration_min
    FROM task_runtime_state r
    JOIN tasks t ON r.task_id = t.task_id
    WHERE {where}
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def compute_iqr_bounds(values: list[float]) -> tuple[float, float]:
    """Return (lower_fence, upper_fence) using 1.5*IQR rule.

    Returns (-inf, inf) if fewer than 4 values — not enough data to detect outliers.
    """
    if len(values) < 4:
        return (float("-inf"), float("inf"))
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    return (q1 - 1.5 * iqr, q3 + 1.5 * iqr)


def identify_outliers(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split tasks into (normal, outliers).

    Outliers are tasks where:
    - duration_min > Q3 + 1.5*IQR for that model (duration outlier), OR
    - retry_count >= MAX_RETRY_WALL (hit retry ceiling)

    IQR is computed per model so slow models aren't penalized vs fast ones.
    """
    if not tasks:
        return [], []

    # Compute per-model IQR bounds
    by_model: dict[str, list[float]] = defaultdict(list)
    for t in tasks:
        if t["duration_min"] is not None:
            by_model[t["model"]].append(t["duration_min"])

    model_upper: dict[str, float] = {}
    for m, durations in by_model.items():
        _, upper = compute_iqr_bounds(durations)
        model_upper[m] = upper

    normal = []
    outliers = []
    for t in tasks:
        reasons = []
        dur = t["duration_min"]
        upper = model_upper.get(t["model"], float("inf"))
        if dur is not None and dur > upper:
            reasons.append(f"duration outlier (>{upper:.1f} min IQR fence)")
        if t["retry_count"] >= MAX_RETRY_WALL:
            reasons.append(f"hit retry wall (retries={t['retry_count']})")
        if reasons:
            outliers.append({**t, "outlier_reasons": reasons})
        else:
            normal.append(t)

    return normal, outliers


# ---------------------------------------------------------------------------
# Aggregations over filtered task list
# ---------------------------------------------------------------------------


def aggregate_model_summary(tasks: list[dict]) -> list[dict]:
    """Compute per-model aggregate stats from a filtered task list."""
    by_model: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        by_model[t["model"]].append(t)

    results = []
    for m, m_tasks in sorted(by_model.items(), key=lambda x: -len(x[1])):
        durations = [t["duration_min"] for t in m_tasks if t["duration_min"] is not None]
        retries = [t["retry_count"] for t in m_tasks]
        completed = len(m_tasks)
        first_ok = sum(1 for t in m_tasks if t["retry_count"] == 0)
        audit_count = sum(1 for t in m_tasks if t.get("task_type") == "audit")
        results.append({
            "model": m,
            "tasks_completed": completed,
            "avg_duration_min": round(sum(durations) / len(durations), 1) if durations else None,
            "min_duration_min": round(min(durations), 1) if durations else None,
            "max_duration_min": round(max(durations), 1) if durations else None,
            "avg_retries": round(sum(retries) / len(retries), 2) if retries else 0,
            "first_attempt_rate": round(first_ok / completed * 100, 1) if completed else 0,
            "first_attempt_success": first_ok,
            "needed_retry": sum(1 for t in m_tasks if t["retry_count"] > 0),
            "audit_tasks": audit_count,
            "impl_tasks": completed - audit_count,
        })
    return results


def aggregate_repo_breakdown(tasks: list[dict]) -> list[dict]:
    """Compute per-repo aggregate stats from a filtered task list."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        by_repo[t["repo"]].append(t)

    results = []
    for repo, r_tasks in sorted(by_repo.items(), key=lambda x: -len(x[1])):
        durations = [t["duration_min"] for t in r_tasks if t["duration_min"] is not None]
        retries = [t["retry_count"] for t in r_tasks]
        completed = len(r_tasks)
        first_ok = sum(1 for t in r_tasks if t["retry_count"] == 0)
        results.append({
            "repo": repo,
            "tasks_completed": completed,
            "avg_duration_min": round(sum(durations) / len(durations), 1) if durations else None,
            "avg_retries": round(sum(retries) / len(retries), 2) if retries else 0,
            "first_attempt_rate": round(first_ok / completed * 100, 1) if completed else 0,
        })
    return results


def aggregate_retry_distribution(tasks: list[dict]) -> list[dict]:
    """Compute retry count distribution from a filtered task list."""
    counts: dict[tuple[str, int], int] = defaultdict(int)
    for t in tasks:
        counts[(t["model"], t["retry_count"])] += 1

    results = []
    for (model, retry_count), task_count in sorted(counts.items()):
        results.append({"model": model, "retry_count": retry_count, "task_count": task_count})
    return results


def slowest_from_tasks(tasks: list[dict], limit: int = 10) -> list[dict]:
    """Return the slowest tasks from a filtered task list."""
    with_dur = [t for t in tasks if t["duration_min"] is not None]
    with_dur.sort(key=lambda t: t["duration_min"], reverse=True)
    return with_dur[:limit]


# ---------------------------------------------------------------------------
# Audit queries (unchanged — these don't need per-task IQR filtering)
# ---------------------------------------------------------------------------


def query_audit_outcomes(
    conn: sqlite3.Connection,
    *,
    repo: str | None,
    model: str | None,
    since: str | None,
) -> list[dict]:
    """Per-model audit pass/fail/rework rates."""
    where_clauses = ["r.effective_worker_model IS NOT NULL"]
    params: list = []

    if repo:
        where_clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        where_clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        where_clauses.append("r.finished_at >= ?")
        params.append(since)

    where = " AND ".join(where_clauses)

    sql = f"""
    SELECT
        r.effective_worker_model AS model,
        COUNT(*) AS audited_tasks,
        SUM(CASE WHEN audit_events.outcome = 'accepted' THEN 1 ELSE 0 END) AS audit_passed,
        SUM(CASE WHEN audit_events.outcome = 'failed' THEN 1 ELSE 0 END) AS audit_failed,
        SUM(CASE WHEN audit_events.outcome = 'rework' THEN 1 ELSE 0 END) AS audit_rework
    FROM task_runtime_state r
    JOIN tasks t ON r.task_id = t.task_id
    JOIN (
        SELECT
            e.task_id,
            CASE
                WHEN e.event_type = 'planner.audit_accepted' THEN 'accepted'
                WHEN e.event_type = 'planner.task_closed_by_audit' THEN 'accepted'
                WHEN e.event_type IN ('planner.audit_failed', 'planner.task_failed_by_audit') THEN 'failed'
                WHEN e.event_type = 'planner.task_auto_rework' THEN 'rework'
            END AS outcome
        FROM task_events e
        WHERE e.event_type IN (
            'planner.audit_accepted',
            'planner.task_closed_by_audit',
            'planner.audit_failed',
            'planner.task_failed_by_audit',
            'planner.task_auto_rework'
        )
    ) audit_events ON audit_events.task_id = t.task_id
    WHERE {where}
    GROUP BY r.effective_worker_model
    ORDER BY COUNT(*) DESC
    """
    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        total = row["audited_tasks"]
        passed = row["audit_passed"]
        results.append({
            "model": row["model"],
            "audited_tasks": total,
            "audit_passed": passed,
            "audit_failed": row["audit_failed"],
            "audit_rework": row["audit_rework"],
            "audit_pass_rate": round(passed / total * 100, 1) if total else 0,
        })
    return results


def scan_audit_result_files(results_dir: Path) -> dict[str, dict]:
    """Scan .worker-results/ for audit task JSONs. Returns {task_id: {decisions, warnings, discoveries}}."""
    out: dict[str, dict] = {}
    if not results_dir.is_dir():
        return out
    for task_dir in results_dir.iterdir():
        task_id = task_dir.name
        if not task_id.endswith("-AUDIT"):
            continue
        for result_file in task_dir.glob("*.json"):
            try:
                data = json.loads(result_file.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            out[task_id] = {
                "decisions": len(data.get("decisions") or []),
                "warnings": len(data.get("warnings") or []),
                "discoveries": len(data.get("discoveries") or []),
                "summary": (data.get("summary") or "")[:120],
            }
            break  # take the first (most recent) result file per task
    return out


def query_audit_richness(
    conn: sqlite3.Connection,
    results_dir: Path,
    *,
    repo: str | None,
    model: str | None,
    since: str | None,
) -> list[dict]:
    """Per-auditor-model finding richness: avg decisions/warnings on passed vs rework audits."""
    file_data = scan_audit_result_files(results_dir)
    if not file_data:
        return []

    placeholders = ",".join("?" for _ in file_data)
    where_clauses = [
        "t.task_type = 'audit'",
        "r.runtime_status = 'done'",
        f"t.task_id IN ({placeholders})",
    ]
    params: list = list(file_data.keys())

    if repo:
        where_clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        where_clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        where_clauses.append("r.finished_at >= ?")
        params.append(since)

    sql = f"""
    SELECT
        t.task_id,
        r.effective_worker_model AS auditor_model,
        e.event_type
    FROM tasks t
    JOIN task_runtime_state r ON t.task_id = r.task_id
    LEFT JOIN task_events e ON e.task_id = SUBSTR(t.task_id, 1, LENGTH(t.task_id) - 6)
        AND e.event_type IN (
            'planner.audit_accepted', 'planner.task_closed_by_audit',
            'planner.audit_failed', 'planner.task_failed_by_audit',
            'planner.task_auto_rework'
        )
    WHERE {" AND ".join(where_clauses)}
    """
    rows = conn.execute(sql, params).fetchall()

    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        task_id = row["task_id"]
        auditor = row["auditor_model"]
        if not auditor:
            continue
        event = row["event_type"]
        if event in ("planner.audit_accepted", "planner.task_closed_by_audit"):
            outcome = "passed"
        elif event in ("planner.audit_failed", "planner.task_failed_by_audit"):
            outcome = "failed"
        elif event == "planner.task_auto_rework":
            outcome = "rework"
        else:
            outcome = "passed"

        fd = file_data.get(task_id, {})
        buckets[auditor][outcome].append({
            "decisions": fd.get("decisions", 0),
            "warnings": fd.get("warnings", 0),
            "discoveries": fd.get("discoveries", 0),
        })

    results = []
    for auditor, outcomes in sorted(buckets.items()):
        for outcome, entries in sorted(outcomes.items()):
            n = len(entries)
            results.append({
                "auditor_model": auditor,
                "outcome": outcome,
                "count": n,
                "avg_decisions": round(sum(e["decisions"] for e in entries) / n, 1) if n else 0,
                "avg_warnings": round(sum(e["warnings"] for e in entries) / n, 1) if n else 0,
                "avg_discoveries": round(sum(e["discoveries"] for e in entries) / n, 1) if n else 0,
            })
    return results


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_report(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    repo: str | None,
    model: str | None,
    since: str | None,
) -> dict:
    results_dir = db_path.parent / "central_runtime" / ".worker-results"

    raw_tasks = query_tasks_raw(conn, repo=repo, model=model, since=since)
    normal_tasks, outlier_tasks = identify_outliers(raw_tasks)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"repo": repo, "model": model, "since": since},
        "excluded_outliers": len(outlier_tasks),
        "model_summary": aggregate_model_summary(normal_tasks),
        "audit_outcomes": query_audit_outcomes(conn, repo=repo, model=model, since=since),
        "audit_richness": query_audit_richness(conn, results_dir, repo=repo, model=model, since=since),
        "repo_breakdown": aggregate_repo_breakdown(normal_tasks),
        "retry_distribution": aggregate_retry_distribution(normal_tasks),
        "slowest_tasks": slowest_from_tasks(normal_tasks, limit=10),
        "outliers": outlier_tasks,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def print_table(headers: list[str], rows: list[list], alignments: list[str] | None = None):
    """Simple aligned table printer."""
    if not rows:
        print("  (no data)")
        return
    col_widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        str_row = [str(v) for v in row]
        str_rows.append(str_row)
        for i, v in enumerate(str_row):
            col_widths[i] = max(col_widths[i], len(v))

    if not alignments:
        alignments = ["l"] * len(headers)

    def fmt_cell(val: str, width: int, align: str) -> str:
        if align == "r":
            return val.rjust(width)
        return val.ljust(width)

    header_line = "  ".join(fmt_cell(h, col_widths[i], "l") for i, h in enumerate(headers))
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    print(f"  {header_line}")
    print(f"  {sep_line}")
    for str_row in str_rows:
        line = "  ".join(fmt_cell(str_row[i], col_widths[i], alignments[i]) for i in range(len(headers)))
        print(f"  {line}")


def print_report(report: dict):
    filters = report["filters"]
    active_filters = [f"{k}={v}" for k, v in filters.items() if v]
    filter_str = f" (filters: {', '.join(active_filters)})" if active_filters else ""
    excluded = report.get("excluded_outliers", 0)
    excl_note = f"  [{excluded} outlier(s) excluded — see bottom]" if excluded else ""
    print(f"\n=== Worker Analytics{filter_str} ==={excl_note}\n")

    # Model summary
    ms = report["model_summary"]
    if ms:
        print("MODEL COMPARISON")
        print_table(
            ["Model", "Done", "Impl", "Audit", "Avg Min", "Min", "Max", "Avg Retry", "1st Attempt %"],
            [[
                r["model"],
                r["tasks_completed"],
                r["impl_tasks"],
                r["audit_tasks"],
                r["avg_duration_min"],
                r["min_duration_min"],
                r["max_duration_min"],
                r["avg_retries"],
                f"{r['first_attempt_rate']}%",
            ] for r in ms],
            ["l", "r", "r", "r", "r", "r", "r", "r", "r"],
        )
        print()

    # Audit outcomes
    ao = report["audit_outcomes"]
    if ao:
        print("AUDIT OUTCOMES BY MODEL")
        print_table(
            ["Model", "Audited", "Passed", "Failed", "Rework", "Pass Rate"],
            [[
                r["model"],
                r["audited_tasks"],
                r["audit_passed"],
                r["audit_failed"],
                r["audit_rework"],
                f"{r['audit_pass_rate']}%",
            ] for r in ao],
            ["l", "r", "r", "r", "r", "r"],
        )
        print()

    # Audit finding richness
    ar = report.get("audit_richness", [])
    if ar:
        print("AUDIT FINDING RICHNESS (avg findings per audit report)")
        print_table(
            ["Auditor", "Outcome", "N", "Avg Decisions", "Avg Warnings", "Avg Discoveries"],
            [[
                r["auditor_model"],
                r["outcome"],
                r["count"],
                r["avg_decisions"],
                r["avg_warnings"],
                r["avg_discoveries"],
            ] for r in ar],
            ["l", "l", "r", "r", "r", "r"],
        )
        print()

    # Repo breakdown
    rb = report["repo_breakdown"]
    if rb:
        print("REPO BREAKDOWN")
        print_table(
            ["Repo", "Done", "Avg Min", "Avg Retry", "1st Attempt %"],
            [[
                r["repo"],
                r["tasks_completed"],
                r["avg_duration_min"],
                r["avg_retries"],
                f"{r['first_attempt_rate']}%",
            ] for r in rb],
            ["l", "r", "r", "r", "r"],
        )
        print()

    # Retry distribution
    rd = report["retry_distribution"]
    if rd:
        print("RETRY DISTRIBUTION")
        print_table(
            ["Model", "Retries", "Tasks"],
            [[r["model"], r["retry_count"], r["task_count"]] for r in rd],
            ["l", "r", "r"],
        )
        print()

    # Slowest tasks (from normal set — outliers not included)
    st = report["slowest_tasks"]
    if st:
        print("SLOWEST TASKS (top 10, outliers excluded)")
        print_table(
            ["Task", "Repo", "Model", "Retries", "Duration"],
            [[
                r["task_id"],
                r["repo"],
                r["model"],
                r["retry_count"],
                f"{r['duration_min']} min",
            ] for r in st],
            ["l", "l", "l", "r", "r"],
        )
        print()

    # Outliers section
    outliers = report.get("outliers", [])
    if outliers:
        print(f"OUTLIERS ({len(outliers)} excluded from stats above)")
        print_table(
            ["Task", "Repo", "Model", "Retries", "Duration", "Reason"],
            [[
                r["task_id"],
                r["repo"],
                r["model"],
                r["retry_count"],
                f"{r['duration_min']} min" if r["duration_min"] is not None else "?",
                "; ".join(r.get("outlier_reasons", [])),
            ] for r in sorted(outliers, key=lambda x: x.get("duration_min") or 0, reverse=True)],
            ["l", "l", "l", "r", "r", "l"],
        )
        print()


def main():
    parser = argparse.ArgumentParser(
        prog="worker_analytics.py",
        description="Model efficiency and worker performance analytics from CENTRAL DB.",
    )
    parser.add_argument("--db-path", default=None, help="Override CENTRAL DB path")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--repo", default=None, help="Filter by target repo")
    parser.add_argument("--model", default=None, help="Filter by worker model")
    parser.add_argument(
        "--since",
        default=None,
        help="Time window: 24h, 7d, 2w (default: 7d; use --all-time to remove filter)",
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Include all historical data (no time filter)",
    )
    args = parser.parse_args()

    db_path = resolve_db(args.db_path)
    conn = open_db(db_path)
    since = parse_since(args.since, all_time=args.all_time)

    try:
        report = build_report(conn, db_path, repo=args.repo, model=args.model, since=since)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_report(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
