#!/usr/bin/env python3
"""Portfolio report generator — manager-friendly view of task status by initiative and repo."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "state" / "central_tasks.db"

STATUS_EMOJI = {
    "done": "✓",
    "todo": "○",
    "in_progress": "►",
    "blocked": "✗",
}

RUNTIME_FAILED = {"failed", "timeout"}


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("CENTRAL_TASK_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DB_PATH


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def fetch_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            t.task_id,
            t.title,
            t.summary,
            t.planner_status,
            t.target_repo_id AS repo,
            t.initiative,
            t.priority,
            t.closeout_md,
            t.closed_at,
            r.runtime_status,
            r.last_runtime_error
        FROM tasks t
        LEFT JOIN task_runtime_state r ON r.task_id = t.task_id
        WHERE t.archived_at IS NULL
        ORDER BY t.initiative NULLS LAST, t.target_repo_id, t.priority, t.task_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def build_report(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate tasks into initiative → repo → task groups."""
    by_initiative: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for t in tasks:
        initiative = t["initiative"] or "(untagged)"
        by_initiative[initiative][t["repo"]].append(t)

    initiatives_out: list[dict[str, Any]] = []
    total_done = total_todo = total_in_progress = total_blocked = total_failed = 0

    for initiative, repos in sorted(by_initiative.items()):
        i_done = i_todo = i_in_progress = i_blocked = i_failed = 0
        repos_out: list[dict[str, Any]] = []

        for repo, repo_tasks in sorted(repos.items()):
            tasks_out: list[dict[str, Any]] = []

            for t in repo_tasks:
                ps = t["planner_status"]
                rs = t.get("runtime_status") or ""
                failed = rs in RUNTIME_FAILED and ps != "done"

                summary_line = ""
                if ps == "done":
                    summary_line = truncate(first_line(t.get("closeout_md") or t.get("summary") or ""))
                elif failed:
                    err = t.get("last_runtime_error") or ""
                    summary_line = truncate(err or "runtime failure — no error recorded")
                else:
                    summary_line = truncate(first_line(t.get("summary") or ""))

                task_entry: dict[str, Any] = {
                    "task_id": t["task_id"],
                    "title": t["title"],
                    "planner_status": ps,
                    "runtime_status": rs or None,
                    "failed": failed,
                    "summary": summary_line,
                }
                if t.get("closed_at"):
                    task_entry["closed_at"] = t["closed_at"]

                tasks_out.append(task_entry)

                if failed:
                    i_failed += 1
                elif ps == "done":
                    i_done += 1
                elif ps == "todo":
                    i_todo += 1
                elif ps == "in_progress":
                    i_in_progress += 1
                elif ps == "blocked":
                    i_blocked += 1

            r_total = len(tasks_out)
            r_done = sum(1 for t in tasks_out if t["planner_status"] == "done" and not t["failed"])
            repos_out.append(
                {
                    "repo": repo,
                    "tasks": tasks_out,
                    "counts": {
                        "total": r_total,
                        "done": r_done,
                        "todo": sum(1 for t in tasks_out if t["planner_status"] == "todo"),
                        "in_progress": sum(1 for t in tasks_out if t["planner_status"] == "in_progress"),
                        "blocked": sum(1 for t in tasks_out if t["planner_status"] == "blocked"),
                        "failed": sum(1 for t in tasks_out if t["failed"]),
                    },
                    "completion_pct": round(100 * r_done / r_total) if r_total else 0,
                }
            )

        i_total = i_done + i_todo + i_in_progress + i_blocked + i_failed
        total_done += i_done
        total_todo += i_todo
        total_in_progress += i_in_progress
        total_blocked += i_blocked
        total_failed += i_failed

        initiatives_out.append(
            {
                "initiative": initiative,
                "repos": repos_out,
                "counts": {
                    "total": i_total,
                    "done": i_done,
                    "todo": i_todo,
                    "in_progress": i_in_progress,
                    "blocked": i_blocked,
                    "failed": i_failed,
                },
                "completion_pct": round(100 * i_done / i_total) if i_total else 0,
            }
        )

    grand_total = total_done + total_todo + total_in_progress + total_blocked + total_failed
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "initiatives": initiatives_out,
        "totals": {
            "total": grand_total,
            "done": total_done,
            "todo": total_todo,
            "in_progress": total_in_progress,
            "blocked": total_blocked,
            "failed": total_failed,
        },
        "completion_pct": round(100 * total_done / grand_total) if grand_total else 0,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    t = report["totals"]
    lines += [
        f"# Portfolio Status Report",
        f"",
        f"Generated: {report['generated_at']}",
        f"",
        f"**Overall:** {t['done']}/{t['total']} done ({report['completion_pct']}%)"
        + (f"  |  {t['in_progress']} in progress" if t["in_progress"] else "")
        + (f"  |  {t['blocked']} blocked" if t["blocked"] else "")
        + (f"  |  **{t['failed']} failed**" if t["failed"] else ""),
        f"",
        "---",
        "",
    ]

    for initiative in report["initiatives"]:
        ic = initiative["counts"]
        lines += [
            f"## {initiative['initiative']}",
            f"",
            f"**{ic['done']}/{ic['total']} done** ({initiative['completion_pct']}%)"
            + (f"  ·  {ic['in_progress']} in progress" if ic["in_progress"] else "")
            + (f"  ·  {ic['blocked']} blocked" if ic["blocked"] else "")
            + (f"  ·  **{ic['failed']} failed**" if ic["failed"] else ""),
            "",
        ]

        for repo in initiative["repos"]:
            rc = repo["counts"]
            lines.append(f"### {repo['repo']}  ({rc['done']}/{rc['total']} done)")
            lines.append("")

            for task in repo["tasks"]:
                ps = task["planner_status"]
                failed = task["failed"]
                if failed:
                    status_label = "FAILED"
                else:
                    status_label = ps.upper()

                bullet = f"- **[{status_label}]** `{task['task_id']}` {task['title']}"
                lines.append(bullet)
                if task["summary"]:
                    lines.append(f"  _{task['summary']}_")
            lines.append("")

    return "\n".join(lines)


def render_text(report: dict[str, Any]) -> str:
    """Plain-text terminal-friendly rendering."""
    lines: list[str] = []
    t = report["totals"]
    lines += [
        "PORTFOLIO STATUS REPORT",
        "=" * 60,
        f"Generated: {report['generated_at']}",
        f"Overall:   {t['done']}/{t['total']} done ({report['completion_pct']}%)"
        + (f"  {t['in_progress']} in-progress" if t["in_progress"] else "")
        + (f"  {t['blocked']} blocked" if t["blocked"] else "")
        + (f"  {t['failed']} FAILED" if t["failed"] else ""),
        "",
    ]

    for initiative in report["initiatives"]:
        ic = initiative["counts"]
        lines += [
            f"{'=' * 60}",
            f"INITIATIVE: {initiative['initiative'].upper()}",
            f"  {ic['done']}/{ic['total']} done ({initiative['completion_pct']}%)"
            + (f"  {ic['in_progress']} in-progress" if ic["in_progress"] else "")
            + (f"  {ic['blocked']} blocked" if ic["blocked"] else "")
            + (f"  {ic['failed']} FAILED" if ic["failed"] else ""),
            "",
        ]

        for repo in initiative["repos"]:
            rc = repo["counts"]
            lines.append(f"  [{repo['repo']}]  {rc['done']}/{rc['total']} done")

            for task in repo["tasks"]:
                ps = task["planner_status"]
                failed = task["failed"]
                label = "FAIL" if failed else ps[:4].upper()
                lines.append(f"    {label:4s}  {task['task_id']:20s}  {task['title']}")
                if task["summary"]:
                    lines.append(f"          → {task['summary']}")
            lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a manager-friendly portfolio status report."
    )
    parser.add_argument("--db-path", default=None, help="SQLite DB path.")
    parser.add_argument(
        "--initiative",
        default=None,
        help="Filter to a single initiative.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Filter to a single repo.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument("--json", action="store_true", help="Alias for --format json.")
    args = parser.parse_args(argv)

    if args.json:
        args.format = "json"

    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        print(f"error: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        tasks = fetch_tasks(conn)
    finally:
        conn.close()

    if args.initiative:
        tasks = [t for t in tasks if t.get("initiative") == args.initiative]
    if args.repo:
        tasks = [t for t in tasks if t.get("repo") == args.repo]

    report = build_report(tasks)

    if args.format == "json":
        print(json.dumps(report, indent=2))
    elif args.format == "markdown":
        print(render_markdown(report))
    else:
        print(render_text(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
