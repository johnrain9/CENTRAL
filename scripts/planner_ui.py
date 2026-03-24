#!/usr/bin/env python3
"""
Planner Status UI — CENTRAL v1

Serves a dark-themed live control surface for queue state, active workers,
audits, and repo breakdown. Read-only. Auto-refreshes from canonical CENTRAL
data surfaces.

Usage:
    python3 scripts/planner_ui.py [--port 7099] [--host 127.0.0.1]

Then open http://localhost:7099 in a browser.

Deferred controls (v2+):
    - Dispatcher start/stop/restart
    - In-UI task mutation
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, jsonify, request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
DB_SCRIPT = os.path.join(SCRIPT_DIR, "central_task_db.py")
DISP_SCRIPT = os.path.join(SCRIPT_DIR, "dispatcher_control.py")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_json(cmd: list[str], timeout: int = 15) -> tuple[dict | list, str | None]:
    """Run a command and return (parsed_json, error_string)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return None, f"exit {result.returncode}: {stderr[:300]}"
        text = result.stdout.strip()
        if not text:
            return None, "empty output"
        return json.loads(text), None
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    except json.JSONDecodeError as e:
        return None, f"json parse error: {e}"
    except Exception as e:
        return None, str(e)


def _db(*args) -> tuple[dict | list, str | None]:
    return _run_json([sys.executable, DB_SCRIPT] + list(args))


def _disp(*args) -> tuple[dict | list, str | None]:
    return _run_json([sys.executable, DISP_SCRIPT] + list(args))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_db() -> sqlite3.Connection | None:
    """Open a read-only connection to the CENTRAL task DB, or None if absent."""
    db_path = os.environ.get(
        "CENTRAL_TASK_DB_PATH",
        os.path.join(REPO_ROOT, "state", "central_tasks.db"),
    )
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _run_text(cmd: list[str], timeout: int = 15, cwd: str | None = None) -> tuple[str | None, str | None]:
    """Run a command and return (stdout_text, error_string)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return None, f"exit {result.returncode}: {stderr[:300]}"
        return result.stdout.strip(), None
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    except Exception as e:
        return None, str(e)


def _git_last_commit(repo_root: str | None) -> str:
    if not repo_root:
        return "unknown"
    out, err = _run_text(["git", "-C", repo_root, "log", "-1", "--format=%ar"])
    if err or not out:
        return "unknown"
    return out


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _weekly_completed_count(tasks: list[dict[str, Any]]) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    count = 0
    for task in tasks:
        if task.get("planner_status") != "done":
            continue
        closed_at = _parse_iso(task.get("closed_at"))
        if closed_at and closed_at >= cutoff:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def build_ui_payload() -> dict:
    errors = []

    # Dispatcher status (always JSON output)
    disp_status, err = _disp("status")
    if err:
        errors.append(f"dispatcher/status: {err}")
        disp_status = {}

    # Active workers
    workers_data, err = _disp("workers", "--json")
    if err:
        errors.append(f"dispatcher/workers: {err}")
        workers_data = {}

    # Planner panel — the richest single view
    panel, err = _db("view-planner-panel", "--json")
    if err:
        errors.append(f"view-planner-panel: {err}")
        panel = {}

    # Portfolio summary (per-repo counts, planner counts)
    summary, err = _db("view-summary", "--json")
    if err:
        errors.append(f"view-summary: {err}")
        summary = {}

    # Needs-attention items (failed runtime, pending review)
    review, err = _db("view-review", "--json")
    if err:
        errors.append(f"view-review: {err}")
        review = []

    # Blocked tasks
    blocked, err = _db("view-blocked", "--json")
    if err:
        errors.append(f"view-blocked: {err}")
        blocked = []

    # Repo registry + all tasks for repo-initiative mapping
    repos, err = _db("repo-list", "--json")
    if err:
        errors.append(f"repo-list: {err}")
        repos = []

    all_tasks, err = _db("task-list", "--json")
    if err:
        errors.append(f"task-list: {err}")
        all_tasks = []

    health_rows, err = _db("health-snapshot-latest", "--json")
    if err:
        errors.append(f"health-snapshot-latest: {err}")
        health_rows = []

    weekly_completed = _weekly_completed_count(all_tasks)

    return {
        "generated_at": _now_iso(),
        "errors": errors,
        "dispatcher": _shape_dispatcher(disp_status),
        "workers": _shape_workers(workers_data),
        "actionable": _shape_actionable(panel),
        "needs_attention": _shape_attention(review, panel, blocked, summary.get("mismatches", [])),
        "awaiting_audit": panel.get("awaiting_audit", []),
        "by_repo": _shape_by_repo(summary, all_tasks, repos, health_rows),
        "recent_changes": panel.get("changed_since", []),
        "summary": _shape_summary(disp_status, summary, panel, workers_data, weekly_completed),
    }


def _shape_dispatcher(d: dict) -> dict:
    lock = d.get("lock_payload") or {}
    return {
        "running": d.get("running", False),
        "pid": d.get("pid"),
        "worker_mode": lock.get("worker_mode") or d.get("worker_mode", ""),
        "default_model": lock.get("default_worker_model") or d.get("configured_default_worker_model", ""),
        "default_codex_model": lock.get("default_codex_model") or d.get("configured_default_codex_model", ""),
        "max_workers": lock.get("max_workers") or d.get("configured_max_workers"),
        "claim_policy": d.get("claim_policy", ""),
        "started_at": lock.get("started_at"),
        "poll_interval": lock.get("poll_interval"),
        "heartbeat_seconds": lock.get("heartbeat_seconds"),
        "stale_recovery_seconds": lock.get("stale_recovery_seconds"),
        "eligible_count": d.get("eligible_count"),
        "parked_count": d.get("parked_count"),
        "parked_reason_counts": d.get("parked_reason_counts", {}),
        "next_claim_advisory": d.get("next_claim_advisory_task_id"),
    }


def _shape_workers(d: dict) -> dict:
    return {
        "active": d.get("active_workers", []),
        "recent": d.get("recent_workers", []),
        "summary": d.get("summary", {}),
    }


def _shape_actionable(panel: dict) -> dict:
    eligible = panel.get("eligible_work", [])
    impl = [t for t in eligible if t.get("task_type") != "audit"]
    audit = [t for t in eligible if t.get("task_type") == "audit"]
    ready_audits = panel.get("ready_audits", [])
    return {
        "implementation": impl,
        "audit": audit + ready_audits,
    }


def _shape_attention(review: list, panel: dict, blocked: list, mismatches: list[dict[str, Any]]) -> list:
    """Merge review items, recent failures, and blocked tasks into attention list."""
    def _item_key(item: dict, source: str, idx: int) -> str:
        base = item.get("task_id") or item.get("audit_task_id") or ""
        if base:
            return f"{source}:{base}"
        repo = item.get("repo") or item.get("target_repo_id") or ""
        initiative = item.get("initiative") or ""
        return f"{source}:{repo}:{initiative}:{idx}"

    seen = set()
    items = []

    for r in (review or []):
        key = _item_key(r, "review", len(items))
        if key not in seen:
            seen.add(key)
            items.append({**r, "_source": "review"})

    for f in panel.get("recent_failures", []):
        key = _item_key(f, "failure", len(items))
        if key not in seen:
            seen.add(key)
            items.append({**f, "_source": "failure"})

    for b in (blocked or []):
        key = _item_key(b, "blocked", len(items))
        if key not in seen:
            seen.add(key)
            items.append({**b, "_source": "blocked"})

    for mismatch in (mismatches or []):
        key = _item_key(mismatch, "mismatch", len(items))
        if key not in seen:
            seen.add(key)
            items.append({**mismatch, "_source": "mismatch"})

    return items


def _shape_by_repo(summary: dict, tasks: list[dict], repos: list[dict], health_rows: list[dict]) -> list:
    repo_meta = {r.get("repo_id", ""): r for r in repos or []}
    health_by_repo = {h.get("repo_id", ""): h for h in health_rows or []}
    summary_by_repo = {r.get("repo_id", ""): r for r in summary.get("per_repo", [])}

    initiatives_by_repo: dict[str, dict[str, dict[str, int]]] = {}
    for row in tasks or []:
        repo_id = row.get("repo") or row.get("target_repo_id") or row.get("repo_id") or ""
        if not repo_id:
            continue
        initiative = (row.get("initiative") or "(unassigned)").strip() or "(unassigned)"
        initiatives_by_repo.setdefault(repo_id, {})
        bucket = initiatives_by_repo[repo_id].setdefault(
            initiative,
            {
                "initiative": initiative,
                "total": 0,
                "done": 0,
                "in_progress": 0,
                "blocked": 0,
                "failed": 0,
            },
        )

        bucket["total"] += 1
        planner_status = row.get("planner_status")
        runtime_status = row.get("runtime_status")
        if planner_status == "done":
            bucket["done"] += 1
        else:
            bucket["in_progress"] += 1
        if planner_status == "blocked":
            bucket["blocked"] += 1
        if planner_status == "failed" or runtime_status == "failed":
            bucket["failed"] += 1

    out = []
    for repo_id in sorted(set(summary_by_repo) | set(initiatives_by_repo)):
        summary_row = summary_by_repo.get(repo_id, {})
        initiatives = []
        for bucket in initiatives_by_repo.get(repo_id, {}).values():
            done = bucket.get("done", 0)
            total = bucket.get("total", 0)
            blocked = bucket.get("blocked", 0)
            failed = bucket.get("failed", 0)
            if total <= 0:
                continue
            if total > 0 and done == total:
                color = "green"
            elif failed > 0:
                color = "red"
            elif blocked > 0:
                color = "yellow"
            else:
                color = "blue"
            bucket["color"] = color
            initiatives.append(bucket)

        initiatives.sort(key=lambda b: (-(b["total"]), b["initiative"].lower()))

        repo_info = repo_meta.get(repo_id, {})
        repo_health = health_by_repo.get(repo_id, {})
        test_summary = repo_health.get("test_summary") or {}
        counts = test_summary.get("counts") or {}
        out.append({
            "repo": repo_id,
            "display_name": repo_info.get("display_name", repo_id),
            "repo_root": repo_info.get("repo_root", ""),
            "last_commit": _git_last_commit(repo_info.get("repo_root")),
            "test_health": repo_health.get("overall_status") or repo_health.get("working_status") or "unknown",
            "test_health_freshness": repo_health.get("freshness") or "unknown",
            "test_passed": counts.get("passed"),
            "test_total": counts.get("total"),
            "test_failed": counts.get("failed", 0),
            "coverage_pct": test_summary.get("coverage_percent"),
            "coverage_status": test_summary.get("coverage_status") or "coverage_unknown",
            "total": summary_row.get("total", 0),
            "running": summary_row.get("running", 0),
            "eligible": summary_row.get("eligible", 0),
            "blocked": summary_row.get("blocked", 0),
            "pending_review": summary_row.get("pending_review", 0),
            "initiatives": initiatives,
        })

    # Keep cards deterministic and remove zero-task repos from the display.
    return [r for r in out if (r["initiatives"])]


def _shape_summary(disp: dict, summary: dict, panel: dict, workers_data: dict, weekly_completed: int) -> dict:
    panel_summary = panel.get("summary", {})
    planner = summary.get("planner_counts", {})
    runtime = summary.get("runtime_counts", {})
    active_workers = workers_data.get("active_workers", [])
    lock = disp.get("lock_payload") or {}
    max_w = lock.get("max_workers") or disp.get("configured_max_workers") or 0

    mismatch_count = summary.get("mismatch_count", 0)
    blocked_count = summary.get("blocked_count", 0)
    failure_count = panel_summary.get("stale_count", 0) + runtime.get("failed", 0) + planner.get("failed", 0)

    return {
        "dispatcher_running": disp.get("running", False),
        "max_workers": max_w,
        "active_workers": len(active_workers),
        "idle_slots": max(0, max_w - len(active_workers)) if max_w else 0,
        "eligible_count": panel_summary.get("eligible_count", 0),
        "awaiting_audit_count": panel_summary.get("awaiting_audit_count", 0),
        "failed_audit_count": panel_summary.get("failed_audit_count", 0),
        "blocked_count": blocked_count,
        "mismatch_count": mismatch_count,
        "alert_count": mismatch_count + blocked_count + failure_count,
        "weekly_completed": weekly_completed,
        "stale_count": panel_summary.get("stale_count", 0),
        "recent_changes_count": panel_summary.get("changed_since_count", 0),
        "planner_done": planner.get("done", 0),
        "planner_failed": planner.get("failed", 0),
        "planner_todo": planner.get("todo", 0),
        "runtime_running": runtime.get("running", 0),
        "runtime_failed": runtime.get("failed", 0),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return UI_HTML, 200, {"Content-Type": "text/html"}


@app.route("/api/data")
def api_data():
    payload = build_ui_payload()
    return jsonify(payload)


@app.route("/api/tasks")
def api_tasks():
    data, err = _db("task-list", "--json")
    if err:
        return jsonify({"error": err}), 500
    # Enrich with objective_md (description) so the UI can search across it
    try:
        import sqlite3 as _sqlite3
        db_path = os.environ.get(
            "CENTRAL_TASK_DB_PATH",
            os.path.join(REPO_ROOT, "state", "central_tasks.db"),
        )
        if os.path.exists(db_path):
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = _sqlite3.Row
            rows = conn.execute("SELECT task_id, objective_md FROM tasks").fetchall()
            conn.close()
            desc_map = {row["task_id"]: row["objective_md"] or "" for row in rows}
            for t in data:
                t["description"] = desc_map.get(t["task_id"], "")
    except Exception:
        pass  # descriptions are optional; proceed without them
    return jsonify(data)


@app.route("/api/task/<task_id>")
def api_task(task_id: str):
    data, err = _db("task-show", "--task-id", task_id, "--json")
    if err:
        return jsonify({"error": err}), 500
    meta = data.get("metadata") or {}
    data["audit"] = {
        "relationship_kind": meta.get("relationship_kind"),
        "parent_task_id": meta.get("parent_task_id"),
        "child_audit_task_id": meta.get("child_audit_task_id"),
        "audit_verdict": meta.get("audit_verdict"),
        "audit_required": meta.get("audit_required"),
    }
    return jsonify(data)


@app.route("/api/metrics/all")
def api_metrics_all():
    """Return all metrics dashboard data in a single response."""
    from metrics.query import (
        model_scorecard,
        effort_calibration_crosstab,
        first_pass_rates_by_initiative,
        throughput_daily,
        failure_mode_groups,
        retry_heatmap,
    )

    conn = _open_db()
    if conn is None:
        return jsonify({"error": "database not found"}), 503

    try:
        payload: dict[str, Any] = {
            "generated_at": _now_iso(),
            # Quality: audit-rejection based, not runtime-failure based
            "model_scorecard": model_scorecard(conn),
            "effort_calibration": effort_calibration_crosstab(conn),
            "initiative_health": first_pass_rates_by_initiative(conn),
            # Throughput
            "daily_throughput": throughput_daily(conn, days=30),
            # Ops diagnostics (process failures: quota, timeout, crashes)
            "ops_failure_taxonomy": failure_mode_groups(conn, top_n=20),
            "retry_heatmap": retry_heatmap(conn),
        }
    finally:
        conn.close()

    # Worker richness + audit verdicts from result files (best-effort)
    try:
        from metrics.worker_results import (
            load_results,
            correlate_with_db,
            discovery_density,
            files_changed_stats,
            audit_verdict_distribution,
        )
        results_dir = os.path.join(
            REPO_ROOT, "state", "central_runtime", ".worker-results"
        )
        results = load_results(results_dir, latest_only=True)
        if results:
            conn2 = _open_db()
            if conn2:
                try:
                    results = correlate_with_db(results, conn2)
                finally:
                    conn2.close()
        payload["worker_richness"] = {
            "discovery_density": discovery_density(results, group_by="model"),
            "files_changed_stats": files_changed_stats(results, group_by="model"),
        }
        # Audit verdicts are the primary quality signal
        payload["audit_verdicts"] = audit_verdict_distribution(results, group_by="model")
    except Exception:
        payload["worker_richness"] = {"discovery_density": [], "files_changed_stats": []}
        payload["audit_verdicts"] = []

    return jsonify(payload)


# ---------------------------------------------------------------------------
# Embedded UI
# ---------------------------------------------------------------------------

UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CENTRAL — Planner Status</title>
<style>
:root {
  --bg: #0f1117;
  --bg2: #161b22;
  --bg3: #1c2230;
  --bg4: #232b3a;
  --border: #2d3748;
  --text: #e2e8f0;
  --text-muted: #718096;
  --text-dim: #4a5568;
  --blue: #4299e1;
  --blue-dim: #2b6cb0;
  --green: #48bb78;
  --green-dim: #276749;
  --teal: #38b2ac;
  --amber: #ed8936;
  --amber-dim: #c05621;
  --red: #fc8181;
  --red-dim: #9b2335;
  --orange: #f6ad55;
  --purple: #9f7aea;
  --gray: #718096;
  --font: "SF Mono", "Fira Code", "Cascadia Code", monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 12px;
  line-height: 1.5;
  min-height: 100vh;
}
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── Top Bar ─────────────────────────────────────────────────────────── */
#topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: 8px 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  align-items: center;
}
#topbar .brand {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.05em;
  margin-right: 8px;
  white-space: nowrap;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  border: 1px solid transparent;
}
.pill.running  { background: #1a365d; color: var(--blue);   border-color: var(--blue-dim); }
.pill.stopped  { background: #3d1515; color: var(--red);    border-color: var(--red-dim); }
.pill.warn     { background: #3d2a00; color: var(--amber);  border-color: var(--amber-dim); }
.pill.ok       { background: #1a3a2a; color: var(--green);  border-color: var(--green-dim); }
.pill.neutral  { background: var(--bg3); color: var(--text-muted); border-color: var(--border); }
.pill.teal     { background: #1a3535; color: var(--teal);   border-color: #2c5f5f; }
.pill.red      { background: #3d1515; color: var(--red);    border-color: var(--red-dim); }
.pill.orange   { background: #3d2800; color: var(--orange); border-color: #7a4500; }
.stat-label { color: var(--text-muted); font-weight: 400; }
#refresh-info {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
#refresh-btn {
  cursor: pointer;
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 10px;
}
#refresh-btn:hover { background: var(--bg4); color: var(--text); }
#stale-warn {
  display: none;
  color: var(--amber);
  font-size: 10px;
}

/* ── Dispatcher Settings Bar ─────────────────────────────────────────── */
#settings-bar {
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  padding: 5px 16px;
  font-size: 10px;
  color: var(--text-muted);
  display: flex;
  flex-wrap: wrap;
  gap: 4px 20px;
  align-items: center;
}
#settings-bar span { white-space: nowrap; }
.setting-val { color: var(--text); }

/* ── Layout ──────────────────────────────────────────────────────────── */
#main {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  max-width: 1600px;
  margin: 0 auto;
  padding: 12px 12px 40px;
}

/* ── Section ─────────────────────────────────────────────────────────── */
.section {
  margin-bottom: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg2);
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid var(--border);
}
.section-header:hover { background: var(--bg3); }
.section-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.section-count {
  font-size: 11px;
  font-weight: 700;
  color: var(--text);
  background: var(--bg4);
  border: 1px solid var(--border);
  border-radius: 9999px;
  padding: 0 6px;
  min-width: 22px;
  text-align: center;
}
.collapse-arrow {
  margin-left: auto;
  color: var(--text-dim);
  font-size: 10px;
  transition: transform 0.15s;
}
.section.collapsed .collapse-arrow { transform: rotate(-90deg); }
.section-body { padding: 10px 12px; background: var(--bg); }
.section.collapsed .section-body { display: none; }
.section.hidden { display: none; }
.section.note { opacity: 0.9; }

/* ── Worker Cards ────────────────────────────────────────────────────── */
.workers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 8px;
}
.worker-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--bg2);
  cursor: pointer;
  transition: border-color 0.1s;
}
.worker-card:hover { border-color: var(--blue-dim); }
.worker-card.healthy  { border-left: 3px solid var(--blue); }
.worker-card.stale    { border-left: 3px solid var(--amber); }
.worker-card.warning  { border-left: 3px solid var(--orange); }
.wc-header { display: flex; align-items: baseline; gap: 6px; margin-bottom: 6px; }
.wc-task-id { font-weight: 700; color: var(--blue); font-size: 12px; }
.wc-title { color: var(--text-muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.wc-meta { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; font-size: 10px; }
.wc-key { color: var(--text-dim); }
.wc-val { color: var(--text); }
.wc-val.stale { color: var(--amber); }
.wc-val.flat  { color: var(--text-muted); }
.wc-val.active { color: var(--green); }

/* ── Tables ──────────────────────────────────────────────────────────── */
.task-table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
th {
  text-align: left;
  padding: 5px 8px;
  color: var(--text-dim);
  font-weight: 600;
  text-transform: uppercase;
  font-size: 9px;
  letter-spacing: 0.06em;
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
}
th:hover { color: var(--text-muted); }
th .sort-arrow { margin-left: 3px; opacity: 0.4; }
th.sorted .sort-arrow { opacity: 1; color: var(--blue); }
td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  vertical-align: top;
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--bg2); cursor: pointer; }
tr.selected td { background: #1a2540; }

.tid { font-weight: 700; color: var(--blue); white-space: nowrap; }
.title-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.repo-badge {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 9px;
  background: var(--bg4);
  color: var(--text-muted);
  border: 1px solid var(--border);
  white-space: nowrap;
}

/* Status badges */
.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  white-space: nowrap;
}
.badge-blue    { background: #1a365d; color: var(--blue); }
.badge-green   { background: #1a3a2a; color: var(--green); }
.badge-teal    { background: #1a3535; color: var(--teal); }
.badge-amber   { background: #3d2a00; color: var(--amber); }
.badge-yellow  { background: #4a3f1d; color: #f6ad55; }
.badge-red     { background: #3d1515; color: var(--red); }
.badge-orange  { background: #3d2800; color: var(--orange); }
.badge-gray    { background: var(--bg3); color: var(--gray); }
.badge-purple  { background: #2d1f4a; color: var(--purple); }

/* ── Filter bar ──────────────────────────────────────────────────────── */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 0 8px;
  align-items: center;
}
.filter-bar select,
.filter-bar input {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 3px 6px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 10px;
}
.filter-bar input { min-width: 160px; }
.filter-bar input.kbd-highlight { outline: 2px solid var(--accent, #58a6ff); transition: outline 0.1s; }
.filter-label { color: var(--text-dim); font-size: 10px; }

/* ── Repo cards / initiatives ─────────────────────────────────────────── */
.repo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
}
.repo-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  margin-bottom: 8px;
}
.repo-controls select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 3px 6px;
  border-radius: 4px;
  font-family: var(--font);
  font-size: 10px;
}
.repo-controls label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-muted);
  font-size: 10px;
}
.repo-controls input[type="checkbox"] {
  accent-color: var(--blue);
}
.repo-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--bg2);
}
.repo-name { font-weight: 700; font-size: 12px; color: var(--text); margin-bottom: 6px; }
.repo-meta {
  font-size: 10px;
  display: grid;
  gap: 3px 8px;
  margin-bottom: 10px;
  color: var(--text-muted);
}
.repo-meta span { color: var(--text-dim); }
.repo-meta .kv { display: grid; grid-template-columns: 120px 1fr; }
.repo-initiatives {
  display: grid;
  gap: 8px;
}
.initiative {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  background: var(--bg3);
}
.initiative-head {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: baseline;
  margin-bottom: 4px;
  font-size: 10px;
}
.initiative-name {
  color: var(--text);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.initiative-count {
  color: var(--text-muted);
  font-size: 9px;
}
.progress-wrap {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}
.progress-track {
  position: relative;
  height: 8px;
  border-radius: 999px;
  background: var(--bg4);
  border: 1px solid var(--border);
  overflow: hidden;
}
.progress-fill {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
}
.progress-fill.green { background: var(--green); }
.progress-fill.blue { background: var(--blue); }
.progress-fill.yellow { background: #d69e2e; }
.progress-fill.red { background: var(--red); }
.initiative-text {
  min-width: 50px;
  text-align: right;
  font-size: 9px;
  color: var(--text-dim);
  white-space: nowrap;
}

/* ── Detail Drawer ───────────────────────────────────────────────────── */
#drawer {
  position: fixed;
  top: 0;
  right: -520px;
  width: 520px;
  height: 100vh;
  background: var(--bg2);
  border-left: 1px solid var(--border);
  z-index: 200;
  display: flex;
  flex-direction: column;
  transition: right 0.2s ease;
  overflow: hidden;
}
#drawer.open { right: 0; }
#drawer-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--bg3);
}
#drawer-task-id { font-weight: 700; color: var(--blue); font-size: 14px; }
#drawer-close {
  margin-left: auto;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 16px;
  line-height: 1;
  background: none;
  border: none;
  color: var(--text-muted);
  font-family: var(--font);
}
#drawer-close:hover { color: var(--text); }
#drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  font-size: 11px;
}
.drawer-section { margin-bottom: 14px; }
.drawer-section-title {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  font-weight: 700;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 3px;
}
.drawer-field { display: grid; grid-template-columns: 120px 1fr; gap: 2px; margin-bottom: 4px; }
.df-key { color: var(--text-dim); }
.df-val { color: var(--text); word-break: break-word; }
.drawer-md { color: var(--text-muted); white-space: pre-wrap; line-height: 1.6; font-size: 10px; }
.event-row { padding: 4px 0; border-bottom: 1px solid var(--border); }
.event-row:last-child { border-bottom: none; }
.event-type { color: var(--blue); font-size: 9px; }
.event-age { color: var(--text-dim); font-size: 9px; float: right; }
.event-payload { color: var(--text-muted); font-size: 9px; white-space: pre-wrap; margin-top: 2px; }

/* ── Loading / Error ─────────────────────────────────────────────────── */
#loading-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(15,17,23,0.7);
  z-index: 300;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: var(--text-muted);
}
#error-bar {
  display: none;
  background: #3d1515;
  color: var(--red);
  padding: 6px 16px;
  font-size: 11px;
  border-bottom: 1px solid var(--red-dim);
}
.empty-state {
  color: var(--text-dim);
  padding: 12px 4px;
  font-style: italic;
}

/* ── Recent changes ──────────────────────────────────────────────────── */
.change-row {
  display: grid;
  grid-template-columns: 100px 80px 1fr 90px 60px;
  gap: 4px 10px;
  padding: 4px 0;
  border-bottom: 1px solid var(--border);
  align-items: baseline;
}
.change-row:last-child { border-bottom: none; }
.change-age { color: var(--text-dim); font-size: 10px; }
.change-event { color: var(--text-muted); font-size: 9px; }

/* ── Attention rows ──────────────────────────────────────────────────── */
.attention-row {
  display: grid;
  grid-template-columns: 110px 70px 1fr 140px;
  gap: 4px 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  align-items: baseline;
}
.attention-row:last-child { border-bottom: none; }
.attention-reason { font-size: 10px; color: var(--text-muted); }

/* scroll wrapper for large sections */
.scroll-wrap { max-height: 480px; overflow-y: auto; }

/* ── Tab navigation ──────────────────────────────────────────────────── */
#tab-nav {
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: 0 16px;
  display: flex;
  gap: 0;
}
.tab-btn {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-family: var(--font);
  font-size: 11px;
  font-weight: 600;
  padding: 8px 14px;
  cursor: pointer;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  transition: color 0.1s, border-color 0.1s;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--blue); border-bottom-color: var(--blue); }

/* ── Metrics panel ───────────────────────────────────────────────────── */
#metrics-panel {
  max-width: 1600px;
  margin: 0 auto;
  padding: 12px 12px 40px;
}
.metrics-note {
  font-size: 10px;
  color: var(--text-dim);
  margin-bottom: 6px;
}
#metrics-refresh-ts {
  font-size: 10px;
  color: var(--text-dim);
  margin-bottom: 12px;
  padding: 0 2px;
}
</style>
</head>
<body>

<!-- Top summary bar -->
<div id="topbar">
  <span class="brand">⬡ CENTRAL</span>
  <span id="disp-pill" class="pill neutral">— dispatcher</span>
  <span id="p-weekly" class="pill neutral"><span class="stat-label">weekly done</span>&nbsp;<span id="s-weekly">—</span></span>
  <span id="p-active" class="pill neutral"><span class="stat-label">active workers</span>&nbsp;<span id="s-workers">—</span></span>
  <span id="p-alert" class="pill neutral"><span class="stat-label">alerts</span>&nbsp;<span id="s-alerts">—</span></span>
  <span class="pill neutral"><span class="stat-label">slots</span>&nbsp;<span id="s-slots">—</span></span>
  <span id="p-stale" class="pill neutral"><span class="stat-label">stale</span>&nbsp;<span id="s-stale">—</span></span>

  <div id="refresh-info">
    <span id="stale-warn">⚠ stale data</span>
    <span id="last-refresh-ts">—</span>
    <button id="refresh-btn" onclick="fetchData()">↺ refresh</button>
  </div>
</div>

<!-- Dispatcher settings bar -->
<div id="settings-bar">
  <span>mode: <span class="setting-val" id="cfg-mode">—</span></span>
  <span>model: <span class="setting-val" id="cfg-model">—</span></span>
  <span>max-workers: <span class="setting-val" id="cfg-max">—</span></span>
  <span>claim-policy: <span class="setting-val" id="cfg-claim">—</span></span>
  <span>started: <span class="setting-val" id="cfg-started">—</span></span>
  <span>next-claim: <span class="setting-val" id="cfg-next-claim">—</span></span>
</div>

<!-- Tab navigation -->
<div id="tab-nav">
  <button class="tab-btn active" id="tab-dashboard" onclick="switchTab('dashboard')">Dashboard</button>
  <button class="tab-btn" id="tab-metrics" onclick="switchTab('metrics')">Metrics</button>
</div>

<div id="error-bar"></div>

<div id="main">

  <!-- Active Workers -->
  <div class="section" id="sec-workers">
    <div class="section-header" onclick="toggleSection('sec-workers')">
      <span class="section-title">Active Workers</span>
      <span class="section-count" id="cnt-workers">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="workers-grid" id="workers-grid">
        <div class="empty-state">No active workers.</div>
      </div>
    </div>
  </div>

  <!-- Actionable Now -->
  <div class="section" id="sec-actionable">
    <div class="section-header" onclick="toggleSection('sec-actionable')">
      <span class="section-title">Actionable Now</span>
      <span class="section-count" id="cnt-actionable">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div id="actionable-body">
        <div class="empty-state">Nothing actionable.</div>
      </div>
    </div>
  </div>

  <!-- Needs Attention -->
  <div class="section" id="sec-attention">
    <div class="section-header" onclick="toggleSection('sec-attention')">
      <span class="section-title">Needs Attention</span>
      <span class="section-count" id="cnt-attention">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="scroll-wrap" id="attention-body">
        <div class="empty-state">No attention items.</div>
      </div>
    </div>
  </div>

  <!-- Awaiting Audit -->
  <div class="section" id="sec-audit">
    <div class="section-header" onclick="toggleSection('sec-audit')">
      <span class="section-title">Awaiting Audit</span>
      <span class="section-count" id="cnt-audit">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="task-table-wrap">
        <table id="audit-table">
          <thead>
            <tr>
              <th onclick="sortTable('audit-table',0)">Task <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('audit-table',1)">Repo <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('audit-table',2)">Title <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('audit-table',3)">Audit Task <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('audit-table',4)">Age <span class="sort-arrow">↕</span></th>
            </tr>
          </thead>
          <tbody id="audit-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Repo Cards -->
  <div class="section" id="sec-repo">
    <div class="section-header" onclick="toggleSection('sec-repo')">
      <span class="section-title">Repo Cards</span>
      <span class="section-count" id="cnt-repo">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="repo-controls">
        <span class="filter-label">sort:</span>
        <select id="repo-sort" onchange="handleByRepoControlsChange()">
          <option value="progress_desc">% done (high→low)</option>
          <option value="progress_asc">% done (low→high)</option>
          <option value="active_desc">active count (high→low)</option>
          <option value="active_asc">active count (low→high)</option>
        </select>
        <label for="repo-hide-completed">
          <input id="repo-hide-completed" type="checkbox" onchange="handleByRepoControlsChange()">
          hide completed initiatives
        </label>
      </div>
      <div class="repo-grid" id="repo-grid"></div>
    </div>
  </div>

  <!-- All Tasks -->
  <div class="section" id="sec-tasks">
    <div class="section-header" onclick="toggleSection('sec-tasks')">
      <span class="section-title">Task Explorer</span>
      <span class="section-count" id="cnt-tasks">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="filter-bar">
        <span class="filter-label">filter:</span>
        <input id="f-search" type="text" placeholder="id, title, or description…" oninput="applyFilters()" onkeydown="if(event.key==='Escape'){this.value='';applyFilters();this.blur();}">
        <select id="f-repo" onchange="applyFilters()"><option value="">all repos</option></select>
        <select id="f-pstatus" onchange="applyFilters()">
          <option value="">all planner status</option>
          <option>todo</option><option>in_progress</option>
          <option>awaiting_audit</option><option>done</option><option>failed</option>
        </select>
        <select id="f-rtstatus" onchange="applyFilters()">
          <option value="">all runtime status</option>
          <option>running</option><option>queued</option><option>claimed</option>
          <option>done</option><option>failed</option><option>timeout</option>
        </select>
        <select id="f-type" onchange="applyFilters()">
          <option value="">all types</option>
          <option>implementation</option><option>audit</option>
        </select>
      </div>
      <div class="task-table-wrap scroll-wrap">
        <table id="tasks-table">
          <thead>
            <tr>
              <th onclick="sortTable('tasks-table',0)">Task <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',1)">Repo <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',2)">Title <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',3)">Type <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',4)">P <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',5)">Planner Status <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',6)">Runtime <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',7)">Audit <span class="sort-arrow">↕</span></th>
              <th onclick="sortTable('tasks-table',8)">Completed <span class="sort-arrow">↕</span></th>
            </tr>
          </thead>
          <tbody id="tasks-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Recent Changes -->
  <div class="section collapsed" id="sec-changes">
    <div class="section-header" onclick="toggleSection('sec-changes')">
      <span class="section-title">Recent Changes</span>
      <span class="section-count" id="cnt-changes">0</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="scroll-wrap" id="changes-body">
        <div class="empty-state">No recent changes.</div>
      </div>
    </div>
  </div>

</div><!-- /main -->

<!-- Metrics Tab Panel -->
<div id="metrics-panel" style="display:none">
  <div id="metrics-refresh-ts">Not yet loaded.</div>

  <!-- Model Scorecard -->
  <div class="section" id="msec-scorecard">
    <div class="section-header" onclick="toggleSection('msec-scorecard')">
      <span class="section-title">Model Scorecard</span>
      <span class="section-count" id="msec-scorecard-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div id="metrics-scorecard"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Effort Calibration -->
  <div class="section" id="msec-effort">
    <div class="section-header" onclick="toggleSection('msec-effort')">
      <span class="section-title">Effort Calibration</span>
      <span class="section-count" id="msec-effort-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="metrics-note">Is high effort worth it? Success rate by effort level × model.</div>
      <div id="metrics-effort"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Initiative Health -->
  <div class="section" id="msec-initiative">
    <div class="section-header" onclick="toggleSection('msec-initiative')">
      <span class="section-title">Initiative Health</span>
      <span class="section-count" id="msec-initiative-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div id="metrics-initiative"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Daily Throughput -->
  <div class="section" id="msec-throughput">
    <div class="section-header" onclick="toggleSection('msec-throughput')">
      <span class="section-title">Daily Throughput</span>
      <span class="section-count" id="msec-throughput-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="metrics-note">Completed tasks per day by repo (last 30 days).</div>
      <div id="metrics-throughput"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Retry Heatmap -->
  <div class="section" id="msec-retry">
    <div class="section-header" onclick="toggleSection('msec-retry')">
      <span class="section-title">Retry Heatmap</span>
      <span class="section-count" id="msec-retry-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="metrics-note">Model × task type retry frequency.</div>
      <div id="metrics-retry-heatmap"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Failure Taxonomy -->
  <div class="section" id="msec-failures">
    <div class="section-header" onclick="toggleSection('msec-failures')">
      <span class="section-title">Failure Taxonomy</span>
      <span class="section-count" id="msec-failures-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="metrics-note">Top error patterns from failed/timeout tasks.</div>
      <div id="metrics-failures"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

  <!-- Worker Richness -->
  <div class="section" id="msec-richness">
    <div class="section-header" onclick="toggleSection('msec-richness')">
      <span class="section-title">Worker Richness</span>
      <span class="section-count" id="msec-richness-cnt">—</span>
      <span class="collapse-arrow">▾</span>
    </div>
    <div class="section-body">
      <div class="metrics-note">Discovery and file-change density per task by model.</div>
      <div id="metrics-richness"><div class="empty-state">Loading…</div></div>
    </div>
  </div>

</div><!-- /metrics-panel -->

<!-- Detail Drawer -->
<div id="drawer">
  <div id="drawer-header">
    <span id="drawer-task-id">—</span>
    <span id="drawer-title" style="color:var(--text-muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1"></span>
    <button id="drawer-close" onclick="closeDrawer()">✕</button>
  </div>
  <div id="drawer-body">
    <div class="empty-state">Select a task or worker to inspect.</div>
  </div>
</div>

<div id="loading-overlay">Loading…</div>

<script>
// ─── State ───────────────────────────────────────────────────────────────────
let DATA = null;
let ALL_TASKS = []; // flat list from task-list (lazy loaded)
let FILTER_TASKS = [];
let lastRefreshTime = null;
let refreshTimer = null;
let staleThreshold = 30; // seconds before showing stale warning
let lastRefreshSucceeded = true;
let sortStates = {}; // tableId → {col, asc}
let byRepoSortMode = 'progress_desc';
let byRepoHideCompleted = false;

// ─── Fetch ───────────────────────────────────────────────────────────────────
async function fetchData() {
  clearTimeout(refreshTimer);
  document.getElementById('stale-warn').style.display = 'none';
  try {
    const resp = await fetch('/api/data');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    DATA = await resp.json();
    lastRefreshTime = Date.now();
    lastRefreshSucceeded = true;
    document.getElementById('error-bar').style.display = 'none';
    renderAll(DATA);
    // Also lazy-load task list
    fetchTaskList();
  } catch(e) {
    lastRefreshSucceeded = false;
    showError('Refresh failed: ' + e.message);
  }
  document.getElementById('last-refresh-ts').textContent = fmtTime(new Date());
  scheduleRefresh();
}

function setSectionState(sectionId, hasItems) {
  const section = document.getElementById(sectionId);
  if (!section) return;
  if (hasItems) {
    section.classList.remove('hidden', 'collapsed');
  } else {
    if (sectionId === 'sec-attention') {
      section.classList.add('hidden');
    } else {
      section.classList.add('collapsed');
    }
  }
}

async function fetchTaskList() {
  try {
    const resp = await fetch('/api/tasks');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const tasks = await resp.json();
    buildTaskExplorer(DATA, tasks);
  } catch(e) {
    // fallback: populate from partial data already in DATA
    buildTaskExplorer(DATA, null);
  }
}

function scheduleRefresh() {
  refreshTimer = setTimeout(() => {
    const age = (Date.now() - lastRefreshTime) / 1000;
    if (age > staleThreshold) {
      document.getElementById('stale-warn').style.display = 'inline';
    }
    fetchData();
  }, 30000); // 30 second polling
}

function showError(msg) {
  const bar = document.getElementById('error-bar');
  bar.textContent = msg;
  bar.style.display = 'block';
}

// ─── Render All ──────────────────────────────────────────────────────────────
function renderAll(d) {
  renderSummaryBar(d.summary, d.dispatcher);
  renderSettings(d.dispatcher);
  renderWorkers(d.workers);
  renderActionable(d.actionable);
  renderAttention(d.needs_attention);
  renderAudit(d.awaiting_audit);
  renderByRepo(d.by_repo);
  renderChanges(d.recent_changes);
  if (d.errors && d.errors.length) {
    showError('Data warnings: ' + d.errors.join(' | '));
  }
}

function handleByRepoControlsChange() {
  const sortSelect = document.getElementById('repo-sort');
  const hideToggle = document.getElementById('repo-hide-completed');
  byRepoSortMode = sortSelect ? sortSelect.value : 'progress_desc';
  byRepoHideCompleted = Boolean(hideToggle && hideToggle.checked);
  renderByRepo((DATA && DATA.by_repo) || []);
}

// ─── Summary Bar ─────────────────────────────────────────────────────────────
function renderSummaryBar(s, disp) {
  const dispPill = document.getElementById('disp-pill');
  if (s.dispatcher_running) {
    dispPill.className = 'pill running';
    dispPill.textContent = '● running';
  } else {
    dispPill.className = 'pill stopped';
    dispPill.textContent = '○ stopped';
  }

  document.getElementById('s-weekly').textContent = s.weekly_completed;
  document.getElementById('s-workers').textContent = s.active_workers;
  const alertPill = document.getElementById('p-alert');
  alertPill.className = 'pill ' + (s.alert_count > 0 ? 'red' : 'neutral');
  document.getElementById('s-alerts').textContent = s.alert_count;
  document.getElementById('s-slots').textContent = s.idle_slots + ' idle / ' + s.max_workers + ' max';

  const stalePill = document.getElementById('p-stale');
  stalePill.className = 'pill ' + (s.stale_count > 0 ? 'warn' : 'neutral');
  document.getElementById('s-stale').textContent = s.stale_count;
}

function renderSettings(d) {
  document.getElementById('cfg-mode').textContent = d.worker_mode || '—';
  document.getElementById('cfg-model').textContent = d.default_model || '—';
  document.getElementById('cfg-max').textContent = d.max_workers != null ? d.max_workers : '—';
  document.getElementById('cfg-claim').textContent = d.claim_policy || '—';
  document.getElementById('cfg-started').textContent = d.started_at ? relAge(d.started_at) + ' ago' : '—';
  document.getElementById('cfg-next-claim').textContent = d.next_claim_advisory || '—';
}

// ─── Workers ─────────────────────────────────────────────────────────────────
function renderWorkers(w) {
  const grid = document.getElementById('workers-grid');
  const active = w.active || [];
  document.getElementById('cnt-workers').textContent = active.length;
  setSectionState('sec-workers', active.length > 0);

  if (!active.length) {
    grid.innerHTML = '<div class="empty-state">No active workers.</div>';
    return;
  }

  grid.innerHTML = active.map(worker => {
    const state = worker.observed_state || 'healthy';
    const cls = state === 'healthy' ? 'healthy' : (state === 'stale' ? 'stale' : 'warning');
    const hb = worker.heartbeat || {};
    const log = worker.log || {};
    const logSignal = log.signal || {};
    const rt = worker.runtime || {};

    const hbAge = hb.age_seconds != null ? fmtSeconds(hb.age_seconds) : '—';
    const logSize = log.size_bytes != null ? fmtBytes(log.size_bytes) : '—';
    const logGrowth = log.growth ? (log.growth.bytes_since_last_inspection > 0
      ? '+' + fmtBytes(log.growth.bytes_since_last_inspection)
      : '±0') : '—';
    const logState = logSignal.state || '—';
    const logStale = logSignal.stale;
    const logValClass = logStale ? 'stale' : (logState === 'growing' ? 'active' : 'flat');
    const elapsed = rt.started_at ? relAge(rt.started_at) : '—';
    const model = (worker.worker || {}).model || '—';

    return `<div class="worker-card ${cls}" onclick="openTaskDrawer('${esc(worker.task_id)}')">
      <div class="wc-header">
        <span class="wc-task-id">${esc(worker.task_id)}</span>
        <span class="wc-title">${esc(worker.title || '')}</span>
      </div>
      <div class="wc-meta">
        <span class="wc-key">model</span><span class="wc-val">${esc(model)}</span>
        <span class="wc-key">elapsed</span><span class="wc-val">${esc(elapsed)}</span>
        <span class="wc-key">heartbeat</span><span class="wc-val ${hb.age_seconds > 20 ? 'stale' : ''}">${esc(hbAge)} ago</span>
        <span class="wc-key">log size</span><span class="wc-val">${esc(logSize)}</span>
        <span class="wc-key">log growth</span><span class="wc-val ${logValClass}">${esc(logGrowth)}</span>
        <span class="wc-key">log signal</span><span class="wc-val ${logValClass}">${esc(logState)}</span>
      </div>
    </div>`;
  }).join('');
}

// ─── Actionable ───────────────────────────────────────────────────────────────
function renderActionable(a) {
  const impl = a.implementation || [];
  const audit = a.audit || [];
  const total = impl.length + audit.length;
  document.getElementById('cnt-actionable').textContent = total;
  setSectionState('sec-actionable', total > 0);

  if (!total) {
    document.getElementById('actionable-body').innerHTML = '<div class="empty-state">Nothing actionable right now.</div>';
    return;
  }

  let html = '';
  if (impl.length) {
    html += '<div style="margin-bottom:8px;font-size:10px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.06em">Implementation</div>';
    html += taskTableHTML(impl, 'actionable-impl-table');
  }
  if (audit.length) {
    html += '<div style="margin:12px 0 8px;font-size:10px;font-weight:700;color:var(--teal);text-transform:uppercase;letter-spacing:0.06em">Audit Tasks</div>';
    html += taskTableHTML(audit, 'actionable-audit-table');
  }
  document.getElementById('actionable-body').innerHTML = html;
  attachRowClicks('actionable-impl-table');
  attachRowClicks('actionable-audit-table');
}

// ─── Attention ────────────────────────────────────────────────────────────────
function renderAttention(items) {
  document.getElementById('cnt-attention').textContent = items.length;
  const section = document.getElementById('sec-attention');
  const shouldRender = items.length > 0;
  section.classList.toggle('hidden', !shouldRender);
  if (!items.length) {
    setSectionState('sec-attention', false);
    document.getElementById('attention-body').innerHTML = '<div class="empty-state">No attention items.</div>';
    return;
  }

  section.classList.remove('hidden');

  const rows = items.map(item => {
    const src = item._source || '';
    const srcBadge = src === 'review' ? badge('review','red')
      : src === 'failure' ? badge('failure','amber')
      : src === 'mismatch' ? badge('mismatch','yellow')
      : badge('blocked','orange');
    const tid = item.task_id || '';
    const repo = item.repo || '';
    const title = item.title || '';
    const reason = item.last_error || item.blocker || item.summary || '';
    return `<div class="attention-row" onclick="openTaskDrawer('${esc(tid)}')" style="cursor:pointer">
      <span class="tid">${esc(tid)}</span>
      <span>${repoBadge(repo)}</span>
      <span class="title-cell" title="${esc(title)}">${esc(title)}</span>
      <span class="attention-reason">${srcBadge} ${esc(reason.slice(0,60))}${reason.length > 60 ? '…' : ''}</span>
    </div>`;
  });

  document.getElementById('attention-body').innerHTML =
    `<div style="padding-bottom:4px">${rows.join('')}</div>`;
}

// ─── Awaiting Audit ───────────────────────────────────────────────────────────
function renderAudit(items) {
  document.getElementById('cnt-audit').textContent = items.length;
  setSectionState('sec-audit', items.length > 0);
  const tbody = document.getElementById('audit-tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No tasks awaiting audit.</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(t => `
    <tr onclick="openTaskDrawer('${esc(t.task_id)}')">
      <td class="tid">${esc(t.task_id)}</td>
      <td>${repoBadge(t.repo)}</td>
      <td class="title-cell" title="${esc(t.title)}">${esc(t.title)}</td>
      <td class="tid">${esc(t.audit_task_id || '')}</td>
      <td style="color:var(--text-muted)">${esc(t.age || '')}</td>
    </tr>`).join('');
}

// ─── By Repo ──────────────────────────────────────────────────────────────────
function renderByRepo(repos) {
  const sortSelect = document.getElementById('repo-sort');
  const hideToggle = document.getElementById('repo-hide-completed');
  if (sortSelect) byRepoSortMode = sortSelect.value || byRepoSortMode;
  if (hideToggle) byRepoHideCompleted = hideToggle.checked;

  const visibleInitiatives = (list) =>
    (list || []).filter(item => !byRepoHideCompleted || item.done < item.total);

  const initiativeStats = (item) => {
    const total = Number(item.total || 0);
    const done = Number(item.done || 0);
    const active = Math.max(0, total - done);
    const pct = total > 0 ? (done / total) : 0;
    return {total, done, active, pct};
  };

  const repoStats = (repo) => {
    const initiatives = visibleInitiatives(repo.initiatives);
    const stats = initiatives.map(initiativeStats);
    const total = stats.reduce((sum, s) => sum + s.total, 0);
    const done = stats.reduce((sum, s) => sum + s.done, 0);
    const active = stats.reduce((sum, s) => sum + s.active, 0);
    const pct = total > 0 ? (done / total) : 0;
    return {total, done, active, pct};
  };

  const compareByMode = (aPct, aActive, bPct, bActive) => {
    if (byRepoSortMode === 'progress_asc') return (aPct - bPct) || (aActive - bActive);
    if (byRepoSortMode === 'active_desc') return (bActive - aActive) || (aPct - bPct);
    if (byRepoSortMode === 'active_asc') return (aActive - bActive) || (bPct - aPct);
    return (bPct - aPct) || (bActive - aActive);
  };

  const sortedRepos = [...repos].sort((a, b) => {
    const aStats = repoStats(a);
    const bStats = repoStats(b);
    const delta = compareByMode(aStats.pct, aStats.active, bStats.pct, bStats.active);
    if (delta !== 0) return delta;
    return String(a.display_name || a.repo || '').localeCompare(String(b.display_name || b.repo || ''));
  });

  document.getElementById('cnt-repo').textContent = sortedRepos.length;
  setSectionState('sec-repo', sortedRepos.length > 0);
  const grid = document.getElementById('repo-grid');
  const testBadge = (r) => {
    const passed = r.test_passed, total = r.test_total, failed = r.test_failed || 0;
    if (total == null || total === 0) {
      const normalized = (r.test_health || '').toLowerCase();
      if (!r.test_health || normalized === 'unknown') return badge('no data', 'gray');
      if (normalized.includes('pass') || normalized === 'healthy') return badge('pass', 'green');
      if (normalized.includes('warn') || normalized.includes('degrade')) return badge('warn', 'yellow');
      return badge('fail', 'red');
    }
    const label = `${passed}/${total}`;
    const color = failed > 0 ? 'red' : 'green';
    return badge(label, color);
  };

  const coverageBadge = (r) => {
    if (r.coverage_pct == null) return badge('cov: ?', 'gray');
    const pct = Math.round(r.coverage_pct);
    const color = pct >= 80 ? 'green' : pct >= 60 ? 'yellow' : 'red';
    return badge(`cov: ${pct}%`, color);
  };

  const initiativeRows = (item) => {
    const stats = initiativeStats(item);
    const pct = stats.total ? Math.max(0, Math.min(100, Math.round((stats.done / stats.total) * 100))) : 0;
    const label = item.done >= item.total ? '✓' : `${pct}%`;
    return `<div class="initiative">
      <div class="initiative-head">
        <span class="initiative-name" title="${esc(item.initiative)}">${esc(item.initiative)}</span>
        <span class="initiative-count">${stats.done}/${stats.total} · ${stats.active} active</span>
      </div>
      <div class="progress-wrap">
        <div class="progress-track">
          <div class="progress-fill ${item.color}" style="width:${pct}%"></div>
        </div>
        <span class="initiative-text">${label}</span>
      </div>
    </div>`;
  };

  if (!sortedRepos.length) {
    grid.innerHTML = '<div class="empty-state">No repository initiatives to display.</div>';
    return;
  }

  grid.innerHTML = sortedRepos.map(r => {
    const initiatives = visibleInitiatives(r.initiatives).sort((a, b) => {
      const aStats = initiativeStats(a);
      const bStats = initiativeStats(b);
      const delta = compareByMode(aStats.pct, aStats.active, bStats.pct, bStats.active);
      if (delta !== 0) return delta;
      return String(a.initiative || '').localeCompare(String(b.initiative || ''));
    }).map(initiativeRows).join('');
    return `<div class="repo-card">
      <div class="repo-name">${esc(r.display_name || r.repo)}</div>
      <div class="repo-meta">
        <span class="kv"><span>commit:</span><span>${esc(r.last_commit || 'unknown')}</span></span>
        <span class="kv"><span>tests:</span><span>${testBadge(r)} ${coverageBadge(r)} ${r.test_health_freshness || ''}</span></span>
      </div>
      <div class="repo-initiatives">${initiatives || '<div class="empty-state">No visible initiatives.</div>'}</div>
    </div>`;
  }).join('');
}

// ─── Recent Changes ───────────────────────────────────────────────────────────
function renderChanges(changes) {
  document.getElementById('cnt-changes').textContent = changes.length;
  setSectionState('sec-changes', changes.length > 0);
  if (!changes.length) {
    document.getElementById('changes-body').innerHTML = '<div class="empty-state">No recent changes.</div>';
    return;
  }
  const rows = changes.map(c => `
    <div class="change-row" onclick="openTaskDrawer('${esc(c.task_id)}')" style="cursor:pointer">
      <span class="tid">${esc(c.task_id)}</span>
      <span>${repoBadge(c.repo)}</span>
      <span class="title-cell" title="${esc(c.title)}">${esc(c.title)}</span>
      <span class="change-event" title="${esc(c.latest_event_type)}">${esc(c.latest_event_type || '')}</span>
      <span class="change-age">${esc(c.change_age || '')}</span>
    </div>`).join('');
  document.getElementById('changes-body').innerHTML = rows;
}

// ─── Task Explorer ────────────────────────────────────────────────────────────
function buildTaskExplorer(d, allTasksFromApi) {
  let tasks;

  if (allTasksFromApi && allTasksFromApi.length) {
    // Full list from /api/tasks — use directly
    tasks = allTasksFromApi.map(t => ({
      task_id: t.task_id,
      title: t.title,
      repo: t.repo || '',
      task_type: t.task_id && t.task_id.endsWith('-AUDIT') ? 'audit' : 'implementation',
      priority: t.priority || '',
      planner_status: t.planner_status || '',
      runtime_status: t.runtime_status || '',
      initiative: t.initiative || '',
      audit_verdict: '',
      closed_at: t.closed_at || null,
      description: t.description || '',
    }));
  } else {
    // Fallback: build from partial DATA sections
    const seen = new Set();
    tasks = [];
    const addTask = (t) => {
      if (!t || !t.task_id || seen.has(t.task_id)) return;
      seen.add(t.task_id);
      tasks.push(t);
    };
    (d.workers.active || []).forEach(w => addTask({
      task_id: w.task_id, title: w.title, repo: '', task_type: '',
      priority: '', planner_status: 'todo', runtime_status: w.runtime_status || 'running', audit_verdict: '',
    }));
    [...(d.actionable.implementation || []), ...(d.actionable.audit || [])].forEach(addTask);
    (d.needs_attention || []).forEach(addTask);
    (d.awaiting_audit || []).forEach(t => addTask({...t, planner_status: 'awaiting_audit'}));
    (d.recent_changes || []).forEach(addTask);
  }

  ALL_TASKS = tasks;

  // Populate repo filter
  const repos = [...new Set(tasks.map(t => t.repo).filter(Boolean))].sort();
  const repoSel = document.getElementById('f-repo');
  const existingVals = [...repoSel.options].map(o => o.value);
  repos.forEach(r => {
    if (!existingVals.includes(r)) {
      const opt = document.createElement('option');
      opt.value = r;
      opt.textContent = r;
      repoSel.appendChild(opt);
    }
  });

  document.getElementById('cnt-tasks').textContent = tasks.length;
  applyFilters();
}

function applyFilters() {
  const search = document.getElementById('f-search').value.toLowerCase();
  const repo = document.getElementById('f-repo').value;
  const pstatus = document.getElementById('f-pstatus').value;
  const rtstatus = document.getElementById('f-rtstatus').value;
  const type = document.getElementById('f-type').value;

  FILTER_TASKS = ALL_TASKS.filter(t => {
    if (search && !t.task_id?.toLowerCase().includes(search) && !t.title?.toLowerCase().includes(search) && !t.description?.toLowerCase().includes(search)) return false;
    if (repo && t.repo !== repo) return false;
    if (pstatus && t.planner_status !== pstatus) return false;
    if (rtstatus && t.runtime_status !== rtstatus) return false;
    if (type && t.task_type !== type) return false;
    return true;
  });

  document.getElementById('cnt-tasks').textContent = FILTER_TASKS.length;
  renderTasksTable(FILTER_TASKS);
}

function fmtClosedAt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const now = new Date();
  const diffMs = now - d;
  const diffH = diffMs / 3600000;
  if (diffH < 24) return fmtTime(d);           // same day: HH:MM
  if (diffH < 24 * 7) return fmtDate(d);       // this week: Mon DD
  return d.toISOString().slice(0, 10);          // older: YYYY-MM-DD
}

function fmtDate(d) {
  return d.toLocaleDateString(undefined, {weekday:'short', month:'short', day:'numeric'});
}

function renderTasksTable(tasks) {
  const tbody = document.getElementById('tasks-tbody');
  if (!tasks.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No tasks match filters.</td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(t => `
    <tr data-task-id="${esc(t.task_id)}" onclick="openTaskDrawer('${esc(t.task_id)}')">
      <td class="tid">${esc(t.task_id)}</td>
      <td>${repoBadge(t.repo)}</td>
      <td class="title-cell" title="${esc(t.title)}">${esc(t.title)}</td>
      <td>${typeBadge(t.task_type)}</td>
      <td style="color:var(--text-muted)">${t.priority || ''}</td>
      <td>${plannerStatusBadge(t.planner_status)}</td>
      <td>${runtimeStatusBadge(t.runtime_status)}</td>
      <td>${auditBadge(t.audit_verdict || t.audit_link)}</td>
      <td style="color:var(--text-muted);white-space:nowrap" data-sort="${t.closed_at || ''}">${fmtClosedAt(t.closed_at)}</td>
    </tr>`).join('');
}

// ─── Detail Drawer ────────────────────────────────────────────────────────────
async function openTaskDrawer(taskId) {
  document.getElementById('drawer-task-id').textContent = taskId;
  document.getElementById('drawer-title').textContent = '';
  document.getElementById('drawer-body').innerHTML = '<div class="empty-state">Loading…</div>';
  document.getElementById('drawer').classList.add('open');

  try {
    const resp = await fetch('/api/task/' + encodeURIComponent(taskId));
    const data = await resp.json();
    if (data.error) {
      document.getElementById('drawer-body').innerHTML = `<div style="color:var(--red)">${esc(data.error)}</div>`;
      return;
    }
    document.getElementById('drawer-title').textContent = data.title || '';
    renderDrawer(data);
  } catch(e) {
    document.getElementById('drawer-body').innerHTML = `<div style="color:var(--red)">Failed: ${esc(e.message)}</div>`;
  }
}

function renderDrawer(d) {
  const rt = d.runtime || {};
  const audit = d.audit || {};
  const events = (d.events || []).slice(0, 10);

  const fields = [
    ['task_id', d.task_id], ['repo', d.target_repo_id],
    ['type', d.task_type], ['priority', d.priority],
    ['planner_status', d.planner_status], ['runtime_status', d.runtime_status],
    ['audit_verdict', audit.audit_verdict], ['audit_task', audit.child_audit_task_id || audit.audit_task_id],
    ['started_at', rt.started_at], ['finished_at', rt.finished_at],
    ['retry_count', rt.retry_count], ['last_error', rt.last_runtime_error],
    ['worker_model', d.effective_worker_model],
  ].filter(([,v]) => v != null && v !== '');

  const fieldHtml = fields.map(([k, v]) =>
    `<div class="drawer-field"><span class="df-key">${esc(k)}</span><span class="df-val">${esc(String(v))}</span></div>`
  ).join('');

  const mdSection = (label, md) => md
    ? `<div class="drawer-section"><div class="drawer-section-title">${label}</div><div class="drawer-md">${esc(md)}</div></div>`
    : '';

  const eventsHtml = events.length ? `
    <div class="drawer-section">
      <div class="drawer-section-title">Recent Events</div>
      ${events.map(e => `
        <div class="event-row">
          <span class="event-type">${esc(e.event_type)}</span>
          <span class="event-age">${esc(fmtRelTs(e.created_at))}</span>
          <div class="event-payload">${esc(JSON.stringify(e.payload || {}, null, 1).slice(0,200))}</div>
        </div>`).join('')}
    </div>` : '';

  const deps = (d.dependencies || []);
  const depsHtml = deps.length
    ? `<div class="drawer-section"><div class="drawer-section-title">Dependencies</div>${deps.map(dep =>
        `<div style="padding:2px 0;color:var(--blue);cursor:pointer" onclick="openTaskDrawer('${esc(dep)}')">${esc(dep)}</div>`
      ).join('')}</div>` : '';

  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-section">
      <div class="drawer-section-title">Task Fields</div>
      ${fieldHtml}
    </div>
    ${mdSection('Objective', d.objective_md)}
    ${mdSection('Context', d.context_md)}
    ${mdSection('Acceptance', d.acceptance_md)}
    ${mdSection('Deliverables', d.deliverables_md)}
    ${depsHtml}
    ${eventsHtml}
  `;
}

function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
}

// ─── Table helpers ────────────────────────────────────────────────────────────
function taskTableHTML(tasks, tableId) {
  const rows = tasks.map(t => `
    <tr data-task-id="${esc(t.task_id)}" onclick="openTaskDrawer('${esc(t.task_id)}')">
      <td class="tid">${esc(t.task_id)}</td>
      <td>${repoBadge(t.repo)}</td>
      <td class="title-cell" title="${esc(t.title)}">${esc(t.title)}</td>
      <td>${typeBadge(t.task_type)}</td>
      <td>${plannerStatusBadge(t.planner_status)}</td>
      <td>${runtimeStatusBadge(t.runtime_status)}</td>
    </tr>`).join('');

  return `<div class="task-table-wrap">
    <table id="${tableId}">
      <thead><tr>
        <th onclick="sortTable('${tableId}',0)">Task <span class="sort-arrow">↕</span></th>
        <th onclick="sortTable('${tableId}',1)">Repo <span class="sort-arrow">↕</span></th>
        <th onclick="sortTable('${tableId}',2)">Title <span class="sort-arrow">↕</span></th>
        <th onclick="sortTable('${tableId}',3)">Type <span class="sort-arrow">↕</span></th>
        <th onclick="sortTable('${tableId}',4)">Planner Status <span class="sort-arrow">↕</span></th>
        <th onclick="sortTable('${tableId}',5)">Runtime <span class="sort-arrow">↕</span></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function attachRowClicks(tableId) {
  // rows already have onclick in HTML; nothing extra needed
}

function sortTable(tableId, colIndex) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const state = sortStates[tableId] || {col: -1, asc: true};
  const asc = state.col === colIndex ? !state.asc : true;
  sortStates[tableId] = {col: colIndex, asc};

  rows.sort((a, b) => {
    const cell_a = a.cells[colIndex];
    const cell_b = b.cells[colIndex];
    // Use data-sort attribute if present (e.g. raw ISO date), else textContent
    const av = cell_a?.dataset.sort ?? cell_a?.textContent.trim() ?? '';
    const bv = cell_b?.dataset.sort ?? cell_b?.textContent.trim() ?? '';
    // Empty values always sort last
    if (!av && bv) return 1;
    if (av && !bv) return -1;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });

  // Update arrow indicators
  table.querySelectorAll('th').forEach((th, i) => {
    const arrow = th.querySelector('.sort-arrow');
    if (!arrow) return;
    if (i === colIndex) {
      th.classList.add('sorted');
      arrow.textContent = asc ? '↑' : '↓';
    } else {
      th.classList.remove('sorted');
      arrow.textContent = '↕';
    }
  });

  rows.forEach(r => tbody.appendChild(r));
}

// ─── Section toggle ───────────────────────────────────────────────────────────
function toggleSection(id) {
  document.getElementById(id).classList.toggle('collapsed');
}

// ─── Badge helpers ────────────────────────────────────────────────────────────
function badge(text, color) {
  return `<span class="badge badge-${color}">${esc(text)}</span>`;
}

function repoBadge(repo) {
  if (!repo) return '';
  return `<span class="repo-badge">${esc(repo)}</span>`;
}

function typeBadge(t) {
  if (t === 'audit') return badge('audit', 'teal');
  if (t === 'implementation') return badge('impl', 'blue');
  return t ? badge(t, 'gray') : '';
}

function plannerStatusBadge(s) {
  const map = {
    todo: 'gray', in_progress: 'blue', awaiting_audit: 'teal',
    done: 'green', failed: 'red', blocked: 'orange',
  };
  return s ? badge(s, map[s] || 'gray') : '';
}

function runtimeStatusBadge(s) {
  if (!s) return '';
  const map = {
    running: 'blue', done: 'green', failed: 'red', queued: 'gray',
    claimed: 'blue', timeout: 'amber', canceled: 'gray',
    pending_review: 'teal',
  };
  return badge(s, map[s] || 'gray');
}

function auditBadge(v) {
  if (!v) return '';
  if (v === 'accepted') return badge('accepted', 'green');
  if (v === 'failed') return badge('failed', 'red');
  if (v === 'pending') return badge('pending', 'teal');
  // if it looks like a task ID
  return `<span style="color:var(--text-dim);font-size:9px">${esc(v)}</span>`;
}

// ─── Formatting ───────────────────────────────────────────────────────────────
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtTime(d) {
  return d.toLocaleTimeString('en-US', {hour12: false});
}

function fmtSeconds(s) {
  if (s == null) return '—';
  if (s < 60) return s.toFixed(1) + 's';
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  if (m < 60) return m + 'm' + (rem > 0 ? rem + 's' : '');
  const h = Math.floor(m / 60);
  return h + 'h' + (m % 60 > 0 ? (m % 60) + 'm' : '');
}

function fmtBytes(b) {
  if (b == null) return '—';
  if (b === 0) return '0B';
  if (b < 1024) return b + 'B';
  if (b < 1048576) return (b / 1024).toFixed(1) + 'KB';
  return (b / 1048576).toFixed(1) + 'MB';
}

function relAge(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  const diffMs = Date.now() - d.getTime();
  return fmtSeconds(diffMs / 1000);
}

function fmtRelTs(isoStr) {
  if (!isoStr) return '—';
  return relAge(isoStr) + ' ago';
}

// ─── Metrics Tab ─────────────────────────────────────────────────────────────
let METRICS_DATA = null;
let activeTab = 'dashboard';

function switchTab(tab) {
  activeTab = tab;
  const dash = document.getElementById('main');
  const mets = document.getElementById('metrics-panel');
  if (dash) dash.style.display = tab === 'dashboard' ? '' : 'none';
  if (mets) mets.style.display = tab === 'metrics' ? '' : 'none';
  document.getElementById('tab-dashboard').classList.toggle('active', tab === 'dashboard');
  document.getElementById('tab-metrics').classList.toggle('active', tab === 'metrics');
  if (tab === 'metrics' && !METRICS_DATA) fetchMetrics();
}

async function fetchMetrics() {
  const infoEl = document.getElementById('metrics-refresh-ts');
  if (infoEl) infoEl.textContent = 'Loading metrics…';
  try {
    const resp = await fetch('/api/metrics/all');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    METRICS_DATA = await resp.json();
    renderMetrics(METRICS_DATA);
    if (infoEl) infoEl.textContent = 'Metrics as of ' + fmtTime(new Date());
  } catch(e) {
    if (infoEl) infoEl.textContent = 'Metrics load failed: ' + esc(e.message);
  }
}

function renderMetrics(m) {
  renderModelScorecard(m.model_scorecard || []);
  renderEffortCalibration(m.effort_calibration || []);
  renderInitiativeHealth(m.initiative_health || []);
  renderDailyThroughput(m.daily_throughput || []);
  renderRetryHeatmap(m.retry_heatmap || []);
  renderFailureTaxonomy(m.ops_failure_taxonomy || []);
  renderAuditVerdicts(m.audit_verdicts || []);
  renderWorkerRichness(m.worker_richness || {});
}

function renderModelScorecard(rows) {
  const el = document.getElementById('metrics-scorecard');
  const cnt = document.getElementById('msec-scorecard-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No completed tasks recorded yet.</div>'; return; }
  const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  const dur = (p50, iqr, n_out) => {
    if (p50 == null) return '—';
    let s = fmtSeconds(p50) + ' p50';
    if (iqr != null) s += ' ±' + fmtSeconds(iqr/2) + ' IQR';
    if (n_out > 0) s += ' <span class="badge badge-amber" title="outliers excluded from IQR">' + n_out + ' outliers</span>';
    return s;
  };
  el.innerHTML = '<div class="task-table-wrap"><table>' +
    '<thead><tr>' +
    '<th>Model</th><th>Done</th><th>Timeouts</th>' +
    '<th>First-pass %</th><th>Rework %</th><th>Avg reworks</th><th>Duration (p50 ± IQR)</th>' +
    '</tr></thead><tbody>' +
    rows.map(r => {
      const fp = r.first_pass_rate;
      const fpcls = fp == null ? 'badge-gray' : fp >= 0.95 ? 'badge-green' : fp >= 0.85 ? 'badge-amber' : 'badge-red';
      return '<tr>' +
        '<td style="white-space:nowrap">' + esc(r.effective_worker_model) + '</td>' +
        '<td><span class="badge badge-green">' + (r.total_done ?? '—') + '</span></td>' +
        '<td>' + (r.timeout_count > 0 ? '<span class="badge badge-amber">' + r.timeout_count + '</span>' : '—') + '</td>' +
        '<td><span class="badge ' + fpcls + '">' + pct(fp) + '</span></td>' +
        '<td>' + pct(r.rework_rate) + '</td>' +
        '<td>' + (r.avg_rework_cycles != null ? r.avg_rework_cycles.toFixed(2) : '—') + '</td>' +
        '<td style="white-space:nowrap">' + dur(r.duration_p50_s, r.duration_iqr_s, r.duration_n_outliers) + '</td>' +
        '</tr>';
    }).join('') +
    '</tbody></table></div>';
}

function renderEffortCalibration(rows) {
  const el = document.getElementById('metrics-effort');
  const cnt = document.getElementById('msec-effort-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No effort data available.</div>'; return; }
  const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  el.innerHTML = '<div class="task-table-wrap"><table>' +
    '<thead><tr><th>Effort</th><th>Model</th><th>Done</th><th>Reworked</th><th>First-pass %</th><th>Rework %</th></tr></thead><tbody>' +
    rows.map(r => {
      const fp = r.first_pass_rate;
      const cls = fp == null ? 'badge-gray' : fp >= 0.95 ? 'badge-green' : fp >= 0.85 ? 'badge-amber' : 'badge-red';
      return '<tr>' +
        '<td><span class="badge badge-gray">' + esc(r.worker_effort) + '</span></td>' +
        '<td style="white-space:nowrap">' + esc(r.effective_worker_model) + '</td>' +
        '<td>' + (r.total_done ?? '—') + '</td>' +
        '<td>' + (r.tasks_reworked ?? '—') + '</td>' +
        '<td><span class="badge ' + cls + '">' + pct(fp) + '</span></td>' +
        '<td>' + pct(r.rework_rate) + '</td>' +
        '</tr>';
    }).join('') +
    '</tbody></table></div>';
}

function renderInitiativeHealth(rows) {
  const el = document.getElementById('metrics-initiative');
  const cnt = document.getElementById('msec-initiative-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No initiative data available.</div>'; return; }
  const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  const bar = (reworked, total) => {
    const p = total > 0 ? Math.min(100, (1 - reworked/total) * 100) : 100;
    const col = p >= 97 ? 'green' : p >= 90 ? 'blue' : 'amber';
    return '<div class="progress-track" style="min-width:80px">' +
      '<div class="progress-fill ' + col + '" style="width:' + p.toFixed(1) + '%"></div>' +
      '</div>';
  };
  el.innerHTML = '<div class="task-table-wrap"><table>' +
    '<thead><tr><th>Initiative</th><th>Done</th><th>Reworked</th><th>First-pass %</th><th>Quality</th></tr></thead><tbody>' +
    rows.map(r =>
      '<tr>' +
      '<td>' + esc(r.initiative) + '</td>' +
      '<td>' + (r.total_done ?? '—') + '</td>' +
      '<td>' + (r.tasks_reworked > 0 ? '<span class="badge badge-amber">' + r.tasks_reworked + '</span>' : '—') + '</td>' +
      '<td>' + pct(r.first_pass_rate) + '</td>' +
      '<td>' + bar(r.tasks_reworked || 0, r.total_done || 0) + '</td>' +
      '</tr>'
    ).join('') +
    '</tbody></table></div>';
}

function renderAuditVerdicts(rows) {
  const el = document.getElementById('metrics-audit-verdicts');
  if (!el) return;
  const cnt = document.getElementById('msec-audit-verdicts-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No audit verdict data available.</div>'; return; }
  const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  el.innerHTML = '<div class="task-table-wrap"><table>' +
    '<thead><tr><th>Audit Model</th><th>Total Audits</th><th>Accepted</th><th>Rework Required</th><th>Acceptance %</th></tr></thead><tbody>' +
    rows.map(r => {
      const ap = r.acceptance_rate;
      const cls = ap == null ? 'badge-gray' : ap >= 0.95 ? 'badge-green' : ap >= 0.85 ? 'badge-amber' : 'badge-red';
      return '<tr>' +
        '<td style="white-space:nowrap">' + esc(r.model || r.group || '—') + '</td>' +
        '<td>' + (r.total_audits ?? '—') + '</td>' +
        '<td><span class="badge badge-green">' + (r.accepted ?? '—') + '</span></td>' +
        '<td>' + (r.rework_required > 0 ? '<span class="badge badge-red">' + r.rework_required + '</span>' : '—') + '</td>' +
        '<td><span class="badge ' + cls + '">' + pct(ap) + '</span></td>' +
        '</tr>';
    }).join('') +
    '</tbody></table></div>';
}

function renderDailyThroughput(rows) {
  const el = document.getElementById('metrics-throughput');
  const cnt = document.getElementById('msec-throughput-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No completions in the last 30 days.</div>'; return; }
  el.innerHTML = '<div class="task-table-wrap scroll-wrap"><table>' +
    '<thead><tr><th>Date</th><th>Repo</th><th>Completed</th></tr></thead><tbody>' +
    rows.map(r =>
      '<tr>' +
      '<td>' + esc(r.date) + '</td>' +
      '<td>' + repoBadge(r.target_repo_id) + '</td>' +
      '<td>' + r.completed + '</td>' +
      '</tr>'
    ).join('') +
    '</tbody></table></div>';
}

function renderRetryHeatmap(rows) {
  const el = document.getElementById('metrics-retry-heatmap');
  const cnt = document.getElementById('msec-retry-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No retry data available.</div>'; return; }
  el.innerHTML = '<div class="task-table-wrap"><table>' +
    '<thead><tr><th>Model</th><th>Task Type</th><th>Total</th><th>Avg Retries</th><th>Max Retries</th></tr></thead><tbody>' +
    rows.map(r => {
      const avg = r.avg_retries || 0;
      const heat = avg > 1 ? 'badge-red' : avg > 0.3 ? 'badge-amber' : 'badge-green';
      return '<tr>' +
        '<td style="white-space:nowrap">' + esc(r.effective_worker_model) + '</td>' +
        '<td>' + typeBadge(r.task_type) + '</td>' +
        '<td>' + r.total + '</td>' +
        '<td><span class="badge ' + heat + '">' + avg + '</span></td>' +
        '<td>' + r.max_retries + '</td>' +
        '</tr>';
    }).join('') +
    '</tbody></table></div>';
}

function renderFailureTaxonomy(rows) {
  const el = document.getElementById('metrics-failures');
  const cnt = document.getElementById('msec-failures-cnt');
  if (cnt) cnt.textContent = rows.length;
  if (!rows.length) { el.innerHTML = '<div class="empty-state">No failures recorded.</div>'; return; }
  el.innerHTML = '<div class="task-table-wrap scroll-wrap"><table>' +
    '<thead><tr><th>Error Pattern</th><th>Count</th><th>Example Tasks</th></tr></thead><tbody>' +
    rows.map(r => {
      const examples = (r.example_task_ids || []).map(id =>
        '<span style="cursor:pointer;color:var(--blue)" onclick="openTaskDrawer(\'' + esc(id) + '\')">' + esc(id) + '</span>'
      ).join(', ');
      return '<tr>' +
        '<td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(r.error_prefix) + '">' + esc(r.error_prefix) + '</td>' +
        '<td><span class="badge badge-red">' + r.count + '</span></td>' +
        '<td style="font-size:9px;color:var(--text-muted)">' + examples + '</td>' +
        '</tr>';
    }).join('') +
    '</tbody></table></div>';
}

function renderWorkerRichness(data) {
  const el = document.getElementById('metrics-richness');
  const cnt = document.getElementById('msec-richness-cnt');
  const dd = (data && data.discovery_density) || [];
  const fc = (data && data.files_changed_stats) || [];
  if (cnt) cnt.textContent = dd.length || '—';
  if (!dd.length && !fc.length) {
    el.innerHTML = '<div class="empty-state">No worker result files found.</div>';
    return;
  }
  let html = '';
  if (dd.length) {
    html += '<div style="margin-bottom:16px">' +
      '<div style="font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);font-weight:700;margin-bottom:6px">Discovery Density</div>' +
      '<div class="task-table-wrap"><table>' +
      '<thead><tr><th>Model</th><th>Tasks</th><th>Avg Discoveries</th><th>Total</th><th>P50</th><th>P90</th></tr></thead><tbody>' +
      dd.map(r =>
        '<tr>' +
        '<td>' + esc(r.model || r.group || '—') + '</td>' +
        '<td>' + r.task_count + '</td>' +
        '<td>' + (r.avg_discoveries != null ? r.avg_discoveries.toFixed(2) : '—') + '</td>' +
        '<td>' + r.total_discoveries + '</td>' +
        '<td>' + (r.p50_discoveries != null ? r.p50_discoveries.toFixed(1) : '—') + '</td>' +
        '<td>' + (r.p90_discoveries != null ? r.p90_discoveries.toFixed(1) : '—') + '</td>' +
        '</tr>'
      ).join('') +
      '</tbody></table></div></div>';
  }
  if (fc.length) {
    html += '<div>' +
      '<div style="font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);font-weight:700;margin-bottom:6px">Files Changed</div>' +
      '<div class="task-table-wrap"><table>' +
      '<thead><tr><th>Model</th><th>Tasks</th><th>Total Files</th><th>Avg Files</th><th>P90</th></tr></thead><tbody>' +
      fc.map(r =>
        '<tr>' +
        '<td>' + esc(r.model || r.group || '—') + '</td>' +
        '<td>' + r.task_count + '</td>' +
        '<td>' + r.total_files_changed + '</td>' +
        '<td>' + (r.avg_files_changed != null ? r.avg_files_changed.toFixed(1) : '—') + '</td>' +
        '<td>' + (r.p90_files_changed != null ? r.p90_files_changed.toFixed(0) : '—') + '</td>' +
        '</tr>'
      ).join('') +
      '</tbody></table></div></div>';
  }
  el.innerHTML = html;
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); return; }
  if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    const s = document.getElementById('f-search');
    if (s) {
      s.focus();
      s.select();
      s.classList.add('kbd-highlight');
      setTimeout(() => s.classList.remove('kbd-highlight'), 600);
    }
  }
});

fetchData().then(() => {
  const hash = window.location.hash.slice(1);
  if (hash) openTaskDrawer(hash);
});

window.addEventListener('hashchange', () => {
  const hash = window.location.hash.slice(1);
  if (hash) openTaskDrawer(hash);
});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CENTRAL Planner Status UI")
    parser.add_argument("--port", type=int, default=7099, help="Port to listen on (default: 7099)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print(f"CENTRAL Planner Status UI")
    print(f"  URL:  http://{args.host}:{args.port}")
    print(f"  Repo: {REPO_ROOT}")
    print(f"  Press Ctrl-C to stop.\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
