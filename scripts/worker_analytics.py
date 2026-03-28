#!/usr/bin/env python3
"""worker_analytics.py — terminal renderer for metrics/query.py.

All SQL lives in metrics/query.py. This script handles CLI parsing, opens
the DB connection, calls the query library, and formats output as tables.

  Model Scorecard / Repo Quality / Retry Distribution / Cycle Times / Failure Modes
      → all-time stats from metrics/query.py (rework-count-based quality, median/IQR duration)

  Audit Outcomes / Audit Richness / Slowest Tasks / Outliers
      → bespoke queries; respect --since / --repo / --model filters (default: last 7 days)

Usage:
    python3 scripts/worker_analytics.py
    python3 scripts/worker_analytics.py --since 24h
    python3 scripts/worker_analytics.py --all-time
    python3 scripts/worker_analytics.py --repo ecosystem
    python3 scripts/worker_analytics.py --model claude-sonnet-4-6
    python3 scripts/worker_analytics.py --json
"""

from __future__ import annotations

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
sys.path.insert(0, str(REPO_ROOT))
from metrics import query as mq  # noqa: E402

DEFAULT_DB = REPO_ROOT / "state" / "central_tasks.db"
MAX_RETRY_WALL = 5


# ---------------------------------------------------------------------------
# CLI / DB helpers
# ---------------------------------------------------------------------------


def resolve_db(path: str | None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("CENTRAL_TASK_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB


def parse_since(value: str | None, *, all_time: bool = False) -> str | None:
    """Convert '24h', '7d', '2w' to ISO timestamp cutoff. Defaults to 7d."""
    if all_time:
        return None
    if not value:
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    m = re.match(r"^(\d+)([hdw])$", value.strip().lower())
    if not m:
        print(f"Invalid --since format: {value!r}. Use e.g. 24h, 7d, 2w.", file=sys.stderr)
        sys.exit(1)
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return (datetime.now(timezone.utc) - delta).isoformat()


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Bespoke queries (audit outcomes, richness, slowest/outliers)
# These are NOT yet in metrics/query.py and support since/repo/model filters.
# ---------------------------------------------------------------------------


def query_audit_outcomes(conn: sqlite3.Connection, *, repo, model, since) -> list[dict]:
    """Per-impl-model audit event counts (passed / failed / rework)."""
    clauses = ["r.effective_worker_model IS NOT NULL"]
    params: list = []
    if repo:
        clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        clauses.append("r.finished_at >= ?")
        params.append(since)

    sql = f"""
    SELECT
        r.effective_worker_model AS model,
        COUNT(*) AS audited,
        SUM(CASE WHEN ae.outcome = 'accepted' THEN 1 ELSE 0 END) AS passed,
        SUM(CASE WHEN ae.outcome = 'failed'   THEN 1 ELSE 0 END) AS failed,
        SUM(CASE WHEN ae.outcome = 'rework'   THEN 1 ELSE 0 END) AS rework
    FROM task_runtime_state r
    JOIN tasks t ON r.task_id = t.task_id
    JOIN (
        SELECT task_id,
            CASE
                WHEN event_type IN ('planner.audit_accepted','planner.task_closed_by_audit') THEN 'accepted'
                WHEN event_type IN ('planner.audit_failed','planner.task_failed_by_audit')   THEN 'failed'
                WHEN event_type = 'planner.task_auto_rework'                                 THEN 'rework'
            END AS outcome
        FROM task_events
        WHERE event_type IN (
            'planner.audit_accepted','planner.task_closed_by_audit',
            'planner.audit_failed','planner.task_failed_by_audit',
            'planner.task_auto_rework'
        )
    ) ae ON ae.task_id = t.task_id
    WHERE {" AND ".join(clauses)}
    GROUP BY r.effective_worker_model
    ORDER BY audited DESC
    """
    results = []
    for row in conn.execute(sql, params).fetchall():
        d = dict(row)
        total = d["audited"]
        d["pass_rate"] = round(d["passed"] / total * 100, 1) if total else 0.0
        results.append(d)
    return results


def scan_audit_result_files(results_dir: Path) -> dict[str, dict]:
    """Read worker result JSONs for audit tasks → {task_id: richness counts}."""
    out: dict[str, dict] = {}
    if not results_dir.is_dir():
        return out
    for task_dir in results_dir.iterdir():
        if not task_dir.name.endswith("-AUDIT"):
            continue
        for result_file in task_dir.glob("*.json"):
            try:
                data = json.loads(result_file.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            out[task_dir.name] = {
                "decisions": len(data.get("decisions") or []),
                "warnings": len(data.get("warnings") or []),
                "discoveries": len(data.get("discoveries") or []),
            }
            break
    return out


def query_audit_richness(
    conn: sqlite3.Connection, results_dir: Path, *, repo, model, since
) -> list[dict]:
    """Avg findings per audit report, grouped by auditor model and outcome."""
    file_data = scan_audit_result_files(results_dir)
    if not file_data:
        return []

    placeholders = ",".join("?" for _ in file_data)
    clauses = [
        "t.task_type = 'audit'",
        "r.runtime_status = 'done'",
        f"t.task_id IN ({placeholders})",
    ]
    params: list = list(file_data.keys())
    if repo:
        clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        clauses.append("r.finished_at >= ?")
        params.append(since)

    sql = f"""
    SELECT t.task_id, r.effective_worker_model AS auditor, e.event_type
    FROM tasks t
    JOIN task_runtime_state r ON t.task_id = r.task_id
    LEFT JOIN task_events e
        ON e.task_id = SUBSTR(t.task_id, 1, LENGTH(t.task_id) - 6)
        AND e.event_type IN (
            'planner.audit_accepted','planner.task_closed_by_audit',
            'planner.audit_failed','planner.task_failed_by_audit',
            'planner.task_auto_rework'
        )
    WHERE {" AND ".join(clauses)}
    """
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in conn.execute(sql, params).fetchall():
        auditor = row["auditor"]
        if not auditor:
            continue
        et = row["event_type"]
        if et in ("planner.audit_accepted", "planner.task_closed_by_audit"):
            outcome = "passed"
        elif et in ("planner.audit_failed", "planner.task_failed_by_audit"):
            outcome = "failed"
        elif et == "planner.task_auto_rework":
            outcome = "rework"
        else:
            outcome = "passed"
        fd = file_data.get(row["task_id"], {})
        buckets[auditor][outcome].append(fd)

    results = []
    for auditor, outcomes in sorted(buckets.items()):
        for outcome, entries in sorted(outcomes.items()):
            n = len(entries)
            results.append({
                "auditor": auditor,
                "outcome": outcome,
                "n": n,
                "avg_decisions": round(sum(e.get("decisions", 0) for e in entries) / n, 1) if n else 0,
                "avg_warnings": round(sum(e.get("warnings", 0) for e in entries) / n, 1) if n else 0,
                "avg_discoveries": round(sum(e.get("discoveries", 0) for e in entries) / n, 1) if n else 0,
            })
    return results


def query_slowest_and_outliers(
    conn: sqlite3.Connection, *, repo, model, since, limit: int = 10
) -> tuple[list[dict], list[dict]]:
    """Per-task durations for slowest-task display and outlier flagging."""
    clauses = [
        "r.runtime_status = 'done'",
        "r.started_at IS NOT NULL",
        "r.finished_at IS NOT NULL",
        "r.effective_worker_model IS NOT NULL",
    ]
    params: list = []
    if repo:
        clauses.append("t.target_repo_id = ?")
        params.append(repo)
    if model:
        clauses.append("r.effective_worker_model = ?")
        params.append(model)
    if since:
        clauses.append("r.finished_at >= ?")
        params.append(since)

    sql = f"""
    SELECT r.task_id, t.target_repo_id AS repo, r.effective_worker_model AS model,
           r.retry_count,
           ROUND((JULIANDAY(r.finished_at) - JULIANDAY(r.started_at)) * 1440, 1) AS duration_min
    FROM task_runtime_state r
    JOIN tasks t ON r.task_id = t.task_id
    WHERE {" AND ".join(clauses)}
    """
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Per-model 1.5×IQR upper fence
    by_model: dict[str, list[float]] = defaultdict(list)
    for t in rows:
        if t["duration_min"] is not None:
            by_model[t["model"]].append(t["duration_min"])
    fences: dict[str, float] = {}
    for m, durs in by_model.items():
        if len(durs) >= 4:
            s = sorted(durs)
            n = len(s)
            q1, q3 = s[n // 4], s[(3 * n) // 4]
            fences[m] = q3 + 1.5 * (q3 - q1)
        else:
            fences[m] = float("inf")

    normal, outliers = [], []
    for t in rows:
        reasons = []
        dur = t["duration_min"]
        fence = fences.get(t["model"], float("inf"))
        if dur is not None and dur > fence:
            reasons.append(f"duration outlier (>{fence:.1f} min fence)")
        if t["retry_count"] >= MAX_RETRY_WALL:
            reasons.append(f"hit retry wall ({t['retry_count']} retries)")
        if reasons:
            outliers.append({**t, "outlier_reasons": reasons})
        else:
            normal.append(t)

    slowest = sorted(normal, key=lambda x: x.get("duration_min") or 0, reverse=True)[:limit]
    outliers_sorted = sorted(outliers, key=lambda x: x.get("duration_min") or 0, reverse=True)
    return slowest, outliers_sorted


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

    # --- metrics/query.py (all-time; post-filter by model/repo in Python) ---
    scorecard = mq.model_scorecard(conn)
    if model:
        scorecard = [r for r in scorecard if r["effective_worker_model"] == model]
    scorecard.sort(key=lambda r: r["total_done"], reverse=True)

    repo_quality = mq.first_pass_rates_by_repo(conn)
    if repo:
        repo_quality = [r for r in repo_quality if r["repo"] == repo]
    repo_quality.sort(key=lambda r: r["total_done"], reverse=True)

    retry_dist = mq.retry_distribution(conn)
    failure_modes = mq.failure_mode_groups(conn, top_n=10)
    cycle = (mq.lead_work_cycle_times(conn) or [{}])[0]

    # --- bespoke queries (support since/repo/model filters) ---
    audit_outcomes = query_audit_outcomes(conn, repo=repo, model=model, since=since)
    audit_richness = query_audit_richness(conn, results_dir, repo=repo, model=model, since=since)
    slowest, outliers = query_slowest_and_outliers(conn, repo=repo, model=model, since=since)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"repo": repo, "model": model, "since": since},
        "scorecard": scorecard,
        "audit_outcomes": audit_outcomes,
        "audit_richness": audit_richness,
        "repo_quality": repo_quality,
        "retry_distribution": retry_dist,
        "failure_modes": failure_modes,
        "cycle_times": cycle,
        "slowest_tasks": slowest,
        "outliers": outliers,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _s_to_min(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds / 60:.1f}"


def print_table(headers: list[str], rows: list[list], alignments: list[str] | None = None) -> None:
    if not rows:
        print("  (no data)")
        return
    col_widths = [len(h) for h in headers]
    str_rows = [[str(v) for v in row] for row in rows]
    for sr in str_rows:
        for i, v in enumerate(sr):
            col_widths[i] = max(col_widths[i], len(v))
    if not alignments:
        alignments = ["l"] * len(headers)

    def fmt(val: str, w: int, a: str) -> str:
        return val.rjust(w) if a == "r" else val.ljust(w)

    print("  " + "  ".join(fmt(h, col_widths[i], "l") for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w for w in col_widths))
    for sr in str_rows:
        print("  " + "  ".join(fmt(sr[i], col_widths[i], alignments[i]) for i in range(len(headers))))


def print_report(report: dict) -> None:
    filters = {k: v for k, v in report["filters"].items() if v}
    filter_str = (
        f" (filters: {', '.join(f'{k}={v}' for k, v in filters.items())})" if filters else ""
    )
    print(f"\n=== Worker Analytics{filter_str} ===")
    print("    scorecard/repo/retry/cycle: all-time  |  audit/slowest: --since window\n")

    # Model scorecard (metrics/query.py — rework-based quality, median/IQR duration)
    sc = report["scorecard"]
    if sc:
        print("MODEL SCORECARD  (impl tasks only; rework-count quality; median duration)")
        print_table(
            ["Model", "Done", "1st Pass%", "Rework%", "P50 min", "P90 min", "Outliers"],
            [
                [
                    r["effective_worker_model"],
                    r["total_done"],
                    f"{round((r['first_pass_rate'] or 0) * 100, 1)}%",
                    f"{round((r['rework_rate'] or 0) * 100, 1)}%",
                    _s_to_min(r["duration_p50_s"]),
                    _s_to_min(r["duration_p90_s"]),
                    r["duration_n_outliers"],
                ]
                for r in sc
            ],
            ["l", "r", "r", "r", "r", "r", "r"],
        )
        print()

    # Audit outcomes (bespoke, time-filtered)
    ao = report["audit_outcomes"]
    if ao:
        print("AUDIT OUTCOMES BY IMPL MODEL")
        print_table(
            ["Model", "Audited", "Passed", "Failed", "Rework", "Pass%"],
            [
                [r["model"], r["audited"], r["passed"], r["failed"], r["rework"], f"{r['pass_rate']}%"]
                for r in ao
            ],
            ["l", "r", "r", "r", "r", "r"],
        )
        print()

    # Audit richness (bespoke, time-filtered)
    ar = report["audit_richness"]
    if ar:
        print("AUDIT FINDING RICHNESS")
        print_table(
            ["Auditor", "Outcome", "N", "Avg Dec", "Avg Warn", "Avg Disc"],
            [
                [r["auditor"], r["outcome"], r["n"], r["avg_decisions"], r["avg_warnings"], r["avg_discoveries"]]
                for r in ar
            ],
            ["l", "l", "r", "r", "r", "r"],
        )
        print()

    # Repo quality (metrics/query.py — rework-based)
    rq = report["repo_quality"]
    if rq:
        print("REPO QUALITY  (rework-based; all-time)")
        print_table(
            ["Repo", "Done", "1st Pass%", "Rework%", "Avg Rework"],
            [
                [
                    r["repo"],
                    r["total_done"],
                    f"{round((r['first_pass_rate'] or 0) * 100, 1)}%",
                    f"{round((r['rework_rate'] or 0) * 100, 1)}%",
                    r["avg_rework_cycles"],
                ]
                for r in rq
            ],
            ["l", "r", "r", "r", "r"],
        )
        print()

    # Cycle times (metrics/query.py)
    ct = report["cycle_times"]
    if ct.get("work_time_p50_s") is not None:
        n = ct.get("work_time_n", "?")
        print(
            f"CYCLE TIMES  (n={n})"
            f"  work P50={_s_to_min(ct.get('work_time_p50_s'))} min"
            f"  work P90={_s_to_min(ct.get('work_time_p90_s'))} min"
            f"  lead P50={_s_to_min(ct.get('lead_time_p50_s'))} min"
            f"  cycle P50={_s_to_min(ct.get('cycle_time_p50_s'))} min\n"
        )

    # Retry distribution (metrics/query.py — all tasks, all-time)
    rd = report["retry_distribution"]
    if rd:
        print("RETRY DISTRIBUTION  (all tasks; all-time)")
        print_table(
            ["Retries", "Tasks", "% of total"],
            [[r["retry_count"], r["tasks"], f"{round((r['pct_of_total'] or 0) * 100, 1)}%"] for r in rd],
            ["r", "r", "r"],
        )
        print()

    # Failure modes (metrics/query.py)
    fm = report["failure_modes"]
    if fm:
        print("FAILURE MODES  (current terminal failures)")
        print_table(
            ["Error prefix", "Count", "Examples"],
            [
                [r["error_prefix"][:60], r["count"], ", ".join(r["example_task_ids"][:3])]
                for r in fm
            ],
            ["l", "r", "l"],
        )
        print()

    # Slowest tasks (bespoke, time-filtered)
    st = report["slowest_tasks"]
    if st:
        print("SLOWEST TASKS  (top 10, outliers excluded)")
        print_table(
            ["Task", "Repo", "Model", "Retries", "Duration"],
            [
                [r["task_id"], r["repo"], r["model"], r["retry_count"], f"{r['duration_min']} min"]
                for r in st
            ],
            ["l", "l", "l", "r", "r"],
        )
        print()

    # Outliers (bespoke, time-filtered)
    outliers = report["outliers"]
    if outliers:
        print(f"OUTLIERS  ({len(outliers)} excluded from slowest tasks above)")
        print_table(
            ["Task", "Repo", "Model", "Retries", "Duration", "Reason"],
            [
                [
                    r["task_id"],
                    r["repo"],
                    r["model"],
                    r["retry_count"],
                    f"{r['duration_min']} min" if r["duration_min"] is not None else "?",
                    "; ".join(r.get("outlier_reasons", [])),
                ]
                for r in outliers
            ],
            ["l", "l", "l", "r", "r", "l"],
        )
        print()


def _print_trend(rows: list[dict], *, weeks: int) -> None:
    print(f"\n=== Weekly Quality Trend (last {weeks} weeks) ===")
    print("  first-pass: zero audit-rework cycles (quota retries excluded)")
    print("  duration: P50 (median); P25–P75 = IQR; includes inter-retry queue wait\n")
    if not rows:
        print("  (no data)")
        return
    print_table(
        ["Week", "Done", "1st-pass%", "Reworked", "P25(m)", "P50(m)", "P75(m)"],
        [
            [
                r["week"],
                r["total_done"],
                f"{r['first_pass_pct']}%" if r["first_pass_pct"] is not None else "—",
                r["tasks_reworked"],
                str(r["p25_duration_min"]) if r["p25_duration_min"] is not None else "—",
                str(r["p50_duration_min"]) if r["p50_duration_min"] is not None else "—",
                str(r["p75_duration_min"]) if r["p75_duration_min"] is not None else "—",
            ]
            for r in rows
        ],
        ["l", "r", "r", "r", "r", "r", "r"],
    )
    # Delta between first and last week
    if len(rows) >= 2:
        first = rows[0]
        last = rows[-1]
        if first["first_pass_pct"] is not None and last["first_pass_pct"] is not None:
            delta = last["first_pass_pct"] - first["first_pass_pct"]
            direction = "+" if delta >= 0 else ""
            print(f"\n  Trend: {first['week']} → {last['week']}  first-pass {direction}{delta:.1f} pp")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="worker_analytics.py",
        description="Worker analytics — thin renderer over metrics/query.py.",
    )
    parser.add_argument("--db-path", default=None, help="Override CENTRAL DB path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--repo", default=None, help="Filter audit/slowest by repo")
    parser.add_argument("--model", default=None, help="Filter by worker model")
    parser.add_argument(
        "--since",
        default=None,
        help="Time window for audit/slowest sections: 24h, 7d, 2w (default: 7d)",
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Remove the 7d default time filter from audit/slowest sections",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Show weekly first-pass rate and duration trend (last 12 weeks)",
    )
    parser.add_argument(
        "--trend-weeks",
        type=int,
        default=12,
        metavar="N",
        help="Number of weeks to include in --trend output (default: 12)",
    )
    args = parser.parse_args()

    db_path = resolve_db(args.db_path)
    conn = open_db(db_path)
    since = parse_since(args.since, all_time=args.all_time)

    try:
        if args.trend:
            trend = mq.weekly_quality_trend(conn, weeks=args.trend_weeks)
            if args.json:
                print(json.dumps(trend, indent=2))
            else:
                _print_trend(trend, weeks=args.trend_weeks)
        else:
            report = build_report(conn, db_path, repo=args.repo, model=args.model, since=since)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print_report(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
