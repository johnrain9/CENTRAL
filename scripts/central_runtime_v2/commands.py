"""CLI commands, status helpers, and entry point for central_runtime_v2.

This module owns:
  - status_payload / worker_status_payload
  - command_* functions
  - build_dispatcher_config / build_parser / main
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from central_runtime_v2.config import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_COORDINATION_PORT,
    DEFAULT_MAX_REMOTE_WORKERS,
    DEFAULT_MAX_REPO_WORKERS,
    DispatcherConfig,
    REPO_ROOT,
)
from central_runtime_v2.dispatcher import CentralDispatcher, connect_initialized
from central_runtime_v2.log import DaemonLog
from central_runtime_v2.model_policy import normalize_optional_string, resolve_default_worker_model
from central_runtime_v2.observation import (
    ACTIVE_WORKER_RUNTIME_STATUSES,
    age_seconds,
    classify_worker_run,
    file_metadata,
    infer_recent_run_id,
    iso_or_none,
    latest_heartbeat_event,
    latest_runtime_event,
    load_status_cache,
    log_growth_payload,
    parse_timestamp,
    save_status_cache,
    seconds_until,
    worker_log_signal,
    worker_run_paths,
    worker_status_text,
)
from central_runtime_v2.paths import (
    build_runtime_paths,
    die,
    ensure_runtime_dirs,
    pid_alive,
    read_lock,
    release_lock,
    resolve_state_dir,
    runtime_paths_payload,
    utc_now,
)

import central_task_db as task_db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECENT_WORKER_RUNTIME_STATUSES: frozenset[str] = frozenset(
    {"pending_review", "failed", "timeout", "canceled", "done"}
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Status payloads
# ---------------------------------------------------------------------------


def status_payload(db_path: Path, paths) -> dict[str, Any]:
    payload = read_lock(paths.lock_path)
    pid = None
    configured_max_workers = None
    worker_mode = None
    configured_default_codex_model = None
    configured_default_worker_model = None
    if payload is not None:
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            pid = None
    running = bool(pid_alive(pid))
    if payload is not None and running:
        try:
            configured_max_workers = int(payload.get("max_workers"))
        except (TypeError, ValueError):
            configured_max_workers = None
        worker_mode = str(payload.get("worker_mode")) if payload.get("worker_mode") else None
        configured_default_worker_model = normalize_optional_string(payload.get("default_worker_model"))
        configured_default_codex_model = (
            normalize_optional_string(payload.get("default_codex_model")) or configured_default_worker_model
        )
        if configured_default_worker_model is None:
            configured_default_worker_model = configured_default_codex_model
    conn = connect_initialized(db_path)
    try:
        runtime_counts = {
            str(row["runtime_status"]): int(row["c"])
            for row in conn.execute(
                "SELECT runtime_status, COUNT(*) AS c FROM task_runtime_state GROUP BY runtime_status"
            ).fetchall()
        }
        active_leases = int(
            conn.execute("SELECT COUNT(*) AS c FROM task_active_leases").fetchone()["c"]
        )
        eligible = len(task_db.order_eligible_snapshots(task_db.fetch_task_snapshots(conn)))
    finally:
        conn.close()
    return {
        "running": running,
        "pid": pid,
        "lock_path": str(paths.lock_path),
        "log_path": str(paths.log_path),
        "db_path": str(db_path),
            "runtime_paths": runtime_paths_payload(paths),
            "configured_max_workers": configured_max_workers,
            "worker_mode": worker_mode,
            "configured_default_codex_model": configured_default_codex_model,
            "configured_default_worker_model": configured_default_worker_model if running else None,
        "active_leases": active_leases,
        "eligible_count": eligible,
        "runtime_counts": runtime_counts,
        "lock_payload": payload,
        "backlog_window_open": task_db.is_backlog_window_open(),
        "schedule_timezone": task_db._SCHEDULE_TIMEZONE,
    }


def worker_status_payload(
    db_path: Path,
    paths,
    *,
    task_id: str | None,
    recent_limit: int,
    recent_hours: float,
) -> dict[str, Any]:
    ensure_runtime_dirs(paths)
    dispatcher = status_payload(db_path, paths)
    now = datetime.now(timezone.utc)
    observed_at = iso_or_none(now) or utc_now()
    cache = load_status_cache(paths.worker_status_cache_path)
    updated_cache: dict[str, dict[str, Any]] = {}
    conn = connect_initialized(db_path)
    try:
        snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
    finally:
        conn.close()

    active_snapshots = [
        snapshot
        for snapshot in snapshots
        if (snapshot.get("lease") is not None)
        or str((snapshot.get("runtime") or {}).get("runtime_status") or "") in ACTIVE_WORKER_RUNTIME_STATUSES
    ]
    active_ids = {snapshot["task_id"] for snapshot in active_snapshots}
    recent_cutoff_seconds = max(0.0, recent_hours) * 3600.0
    recent_candidates: list[dict[str, Any]] = []
    for snapshot in snapshots:
        runtime = snapshot.get("runtime") or {}
        status = str(runtime.get("runtime_status") or "")
        if snapshot["task_id"] in active_ids or status not in RECENT_WORKER_RUNTIME_STATUSES:
            continue
        transition_at = parse_timestamp(runtime.get("last_transition_at"))
        transition_age = age_seconds(now, transition_at)
        if transition_age is not None and transition_age <= recent_cutoff_seconds:
            recent_candidates.append(snapshot)
    recent_candidates.sort(
        key=lambda snapshot: str(((snapshot.get("runtime") or {}).get("last_transition_at") or "")),
        reverse=True,
    )
    recent_snapshots = recent_candidates[:recent_limit]

    def build_entry(snapshot: dict[str, Any]) -> dict[str, Any]:
        conn = connect_initialized(db_path)
        try:
            events = task_db.fetch_latest_events(conn, snapshot["task_id"], limit=20)
            artifacts = task_db.fetch_artifacts(conn, snapshot["task_id"])
        finally:
            conn.close()
        runtime = snapshot.get("runtime") or {}
        lease = snapshot.get("lease") or {}
        lease_metadata = lease.get("metadata") if isinstance(lease, dict) else {}
        supervision = lease_metadata.get("supervision") if isinstance(lease_metadata, dict) else {}
        run_id = lease.get("execution_run_id") or infer_recent_run_id(snapshot["task_id"], artifacts, paths)
        run_paths = worker_run_paths(paths, snapshot["task_id"], run_id, artifacts)
        latest_runtime = latest_runtime_event(events)
        heartbeat_event = latest_heartbeat_event(events)
        heartbeat_at = parse_timestamp(lease.get("last_heartbeat_at")) or parse_timestamp(
            heartbeat_event["created_at"] if heartbeat_event else None
        )
        runtime_event_at = parse_timestamp(latest_runtime["created_at"] if latest_runtime else None)
        lease_expires_at = parse_timestamp(lease.get("lease_expires_at"))
        transition_at = parse_timestamp(runtime.get("last_transition_at"))
        log_info = file_metadata(run_paths["log"], now=now)
        growth_key = f"{snapshot['task_id']}:{run_id or '-'}:{log_info.get('path') or '-'}"
        log_growth, cache_entry = log_growth_payload(cache, growth_key, log_info, observed_at=observed_at)
        updated_cache[growth_key] = cache_entry
        heartbeat = {
            "last_heartbeat_at": iso_or_none(heartbeat_at),
            "age_seconds": age_seconds(now, heartbeat_at),
            "lease_expires_at": iso_or_none(lease_expires_at),
            "seconds_until_lease_expiry": seconds_until(now, lease_expires_at),
        }
        observed_state, reason = classify_worker_run(
            snapshot,
            heartbeat_age=heartbeat["age_seconds"],
            seconds_to_lease_expiry=heartbeat["seconds_until_lease_expiry"],
            log_info=log_info,
            log_growth=log_growth,
            runtime_event_age=age_seconds(now, runtime_event_at),
            transition_age=age_seconds(now, transition_at),
        )
        return {
            "task_id": snapshot["task_id"],
            "title": snapshot["title"],
            "worker_id": lease.get("lease_owner_id") or runtime.get("claimed_by"),
            "run_id": run_id,
            "worker": {
                "backend": supervision.get("worker_backend") if isinstance(supervision, dict) else None,
                "model": (supervision.get("worker_model") or supervision.get("codex_model")) if isinstance(supervision, dict) else None,
                "model_source": (supervision.get("worker_model_source") or supervision.get("codex_model_source")) if isinstance(supervision, dict) else None,
            },
            "runtime_status": runtime.get("runtime_status"),
            "observed_state": observed_state,
            "reason": reason,
            "heartbeat": heartbeat,
            "runtime": {
                "claimed_at": runtime.get("claimed_at"),
                "started_at": runtime.get("started_at"),
                "finished_at": runtime.get("finished_at"),
                "pending_review_at": runtime.get("pending_review_at"),
                "last_transition_at": runtime.get("last_transition_at"),
                "retry_count": runtime.get("retry_count"),
                "last_runtime_error": runtime.get("last_runtime_error"),
            },
            "activity": {
                "latest_runtime_event_type": latest_runtime["event_type"] if latest_runtime else None,
                "latest_runtime_event_at": latest_runtime["created_at"] if latest_runtime else None,
                "latest_runtime_event_age_seconds": age_seconds(now, runtime_event_at),
            },
            "log": {
                **log_info,
                "growth": log_growth,
                "signal": worker_log_signal(snapshot, log_info=log_info, log_growth=log_growth),
            },
            "prompt": file_metadata(run_paths["prompt"], now=now),
            "result": file_metadata(run_paths["result"], now=now),
            "artifacts": [
                {
                    "artifact_kind": artifact.get("artifact_kind"),
                    "path_or_uri": artifact.get("path_or_uri"),
                    "created_at": artifact.get("created_at"),
                }
                for artifact in artifacts[-6:]
            ],
        }

    active_workers = [build_entry(snapshot) for snapshot in active_snapshots]
    recent_workers = [build_entry(snapshot) for snapshot in recent_snapshots]
    healthy_count = sum(1 for worker in active_workers if worker["observed_state"] == "healthy")
    low_activity_count = sum(1 for worker in active_workers if worker["observed_state"] == "low_activity")
    potentially_stuck_count = sum(1 for worker in active_workers if worker["observed_state"] == "potentially_stuck")
    recent_issue_count = sum(1 for worker in recent_workers if worker["observed_state"] == "recent_issue")
    if not active_workers:
        overall_status = "idle"
        headline = "No active worker leases. Use recent runs below for the last known execution context."
    elif potentially_stuck_count:
        overall_status = "potentially_stuck"
        headline = f"{potentially_stuck_count} active worker(s) look stuck or stale."
    else:
        overall_status = "healthy"
        if low_activity_count:
            headline = f"Active workers are not obviously stuck, but {low_activity_count} look quiet."
        else:
            headline = "Active workers show fresh heartbeat or log activity."
    merged_cache = dict(cache)
    merged_cache.update(updated_cache)
    save_status_cache(paths.worker_status_cache_path, merged_cache)
    return {
        "generated_at": observed_at,
        "db_path": str(db_path),
        "state_dir": str(paths.state_dir),
        "runtime_paths": runtime_paths_payload(paths),
        "dispatcher": dispatcher,
        "summary": {
            "overall_status": overall_status,
            "headline": headline,
            "active_count": len(active_workers),
            "healthy_count": healthy_count,
            "low_activity_count": low_activity_count,
            "potentially_stuck_count": potentially_stuck_count,
            "recent_count": len(recent_workers),
            "recent_issue_count": recent_issue_count,
        },
        "active_workers": active_workers,
        "recent_workers": recent_workers,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_status(args: argparse.Namespace) -> int:
    db_path = task_db.resolve_db_path(args.db_path)
    paths = build_runtime_paths(resolve_state_dir(args.state_dir))
    ensure_runtime_dirs(paths)
    payload = status_payload(db_path, paths)
    if args.json:
        print(json_dumps(payload))
    else:
        print(json_dumps(payload))
    return 0


def command_worker_status(args: argparse.Namespace) -> int:
    db_path = task_db.resolve_db_path(args.db_path)
    paths = build_runtime_paths(resolve_state_dir(args.state_dir))
    payload = worker_status_payload(
        db_path,
        paths,
        task_id=args.task_id,
        recent_limit=args.limit,
        recent_hours=args.recent_hours,
    )
    if args.json:
        print(json_dumps(payload))
    else:
        print(worker_status_text(payload))
    return 0


def command_stop(args: argparse.Namespace) -> int:
    paths = build_runtime_paths(resolve_state_dir(args.state_dir))
    payload = read_lock(paths.lock_path)
    if payload is None:
        print("dispatcher_not_running")
        return 0
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        die("invalid dispatcher lock payload")
    if not pid_alive(pid):
        release_lock(paths)
        print("dispatcher_not_running")
        return 0
    os.kill(pid, signal.SIGTERM)
    print(f"stop_signal_sent pid={pid}")
    return 0


def command_tail(args: argparse.Namespace) -> int:
    paths = build_runtime_paths(resolve_state_dir(args.state_dir))
    ensure_runtime_dirs(paths)
    log = DaemonLog(paths)
    is_tty = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    if args.follow:
        log_path = paths.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        initial = log.tail(lines=args.lines, colorize=is_tty)
        if initial:
            print(initial, flush=True)
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)
            try:
                while True:
                    line = fh.readline()
                    if line:
                        out = log.colorize_log_line(line.rstrip("\n")) if is_tty else line.rstrip("\n")
                        print(out, flush=True)
                    else:
                        time.sleep(0.25)
            except KeyboardInterrupt:
                pass
        return 0
    print(log.tail(lines=args.lines, colorize=is_tty))
    return 0


def build_dispatcher_config(args: argparse.Namespace) -> DispatcherConfig:
    explicit_default_model = getattr(args, "default_worker_model", None) or getattr(args, "default_codex_model", None)
    return DispatcherConfig(
        db_path=task_db.resolve_db_path(args.db_path),
        state_dir=resolve_state_dir(args.state_dir),
        max_workers=args.max_workers,
        poll_interval=args.poll_interval,
        heartbeat_seconds=args.heartbeat_seconds,
        status_heartbeat_seconds=args.status_heartbeat_seconds,
        stale_recovery_seconds=args.stale_recovery_seconds,
        worker_mode=args.worker_mode,
        default_worker_model=resolve_default_worker_model(args.worker_mode, explicit_default_model),
        notify=getattr(args, "notify", False),
        audit_worker_model=getattr(args, "audit_worker_model", None),
        remote_workers_enabled=getattr(args, "remote_workers", False),
        coordination_port=getattr(args, "coordination_port", DEFAULT_COORDINATION_PORT),
        max_remote_workers=getattr(args, "max_remote_workers", DEFAULT_MAX_REMOTE_WORKERS),
        max_repo_workers=getattr(args, "max_repo_workers", DEFAULT_MAX_REPO_WORKERS),
    )


def command_run_once(args: argparse.Namespace) -> int:
    dispatcher = CentralDispatcher(build_dispatcher_config(args))
    return dispatcher.run_once(emit_result=True)


def command_daemon(args: argparse.Namespace) -> int:
    dispatcher = CentralDispatcher(build_dispatcher_config(args))
    return dispatcher.run_daemon()


SELF_CHECK_TASK_ID = "CENTRAL-1"


def smoke_task_payload() -> dict[str, Any]:
    return {
        "task_id": SELF_CHECK_TASK_ID,
        "title": "CENTRAL runtime smoke task",
        "summary": "Validate CENTRAL runtime daemon and worker bridge",
        "objective_md": "Run the CENTRAL-native runtime smoke path.",
        "context_md": "Synthetic task used by self-check.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- produce one synthetic worker result",
        "acceptance_md": "- runtime reaches a terminal success state",
        "testing_md": "- stub worker mode only",
        "dispatch_md": "Dispatch locally through central_runtime self-check.",
        "closeout_md": "Review synthetic smoke artifacts only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "initiative": "one-off",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": {"smoke": True, "audit_required": False},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 60,
            "metadata": {},
        },
        "dependencies": [],
    }


def command_self_check(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="central_runtime_selfcheck_") as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "central_tasks.db"
        state_dir = tmp_path / "runtime_state"
        conn = task_db.connect(db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id="CENTRAL",
                    repo_root=str(REPO_ROOT),
                    display_name="CENTRAL",
                )
                task_db.create_task(conn, smoke_task_payload(), actor_kind="self_check", actor_id="central.runtime")
        finally:
            conn.close()
        cfg = DispatcherConfig(
            db_path=db_path,
            state_dir=state_dir,
            max_workers=1,
            poll_interval=0.2,
            heartbeat_seconds=0.5,
            status_heartbeat_seconds=0.5,
            stale_recovery_seconds=0.5,
            worker_mode="stub",
            default_worker_model=DEFAULT_CODEX_MODEL,
        )
        dispatcher = CentralDispatcher(cfg)
        dispatcher.run_once(emit_result=False)
        conn = connect_initialized(db_path)
        try:
            task_row = conn.execute(
                """
                SELECT t.planner_status, rs.runtime_status, rs.last_runtime_error
                FROM tasks t
                LEFT JOIN task_runtime_state rs ON rs.task_id = t.task_id
                WHERE t.task_id = ?
                """,
                (SELF_CHECK_TASK_ID,),
            ).fetchone()
            payload = {
                "db_path": str(db_path),
                "state_dir": str(state_dir),
                "planner_status": task_row["planner_status"] if task_row else None,
                "runtime_status": task_row["runtime_status"] if task_row else None,
                "last_runtime_error": task_row["last_runtime_error"] if task_row else None,
            }
        finally:
            conn.close()
    print(json_dumps(payload))
    return 0


# ---------------------------------------------------------------------------
# Parser and entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CENTRAL-native dispatcher runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show CENTRAL runtime dispatcher status")
    status_parser.add_argument("--db-path")
    status_parser.add_argument("--state-dir")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=command_status)

    worker_status_parser = subparsers.add_parser(
        "worker-status",
        help="Inspect active and recent CENTRAL worker runs with heartbeat and log heuristics",
    )
    worker_status_parser.add_argument("--db-path")
    worker_status_parser.add_argument("--state-dir")
    worker_status_parser.add_argument("--task-id")
    worker_status_parser.add_argument("--limit", type=int, default=5)
    worker_status_parser.add_argument("--recent-hours", type=float, default=24.0)
    worker_status_parser.add_argument("--json", action="store_true")
    worker_status_parser.set_defaults(func=command_worker_status)

    stop_parser = subparsers.add_parser("stop", help="Stop the CENTRAL dispatcher daemon")
    stop_parser.add_argument("--db-path")
    stop_parser.add_argument("--state-dir")
    stop_parser.set_defaults(func=command_stop)

    tail_parser = subparsers.add_parser("tail", help="Show or follow CENTRAL dispatcher log")
    tail_parser.add_argument("--db-path")
    tail_parser.add_argument("--state-dir")
    tail_parser.add_argument("--lines", type=int, default=120)
    tail_parser.add_argument("--follow", action="store_true")
    tail_parser.set_defaults(func=command_tail)

    for name, func in [("run-once", command_run_once), ("daemon", command_daemon)]:
        sub = subparsers.add_parser(name, help=f"{name} for CENTRAL dispatcher")
        sub.add_argument("--db-path")
        sub.add_argument("--state-dir")
        sub.add_argument("--max-workers", type=int, default=1)
        sub.add_argument("--poll-interval", type=float, default=1.0)
        sub.add_argument("--heartbeat-seconds", type=float, default=5.0)
        sub.add_argument("--status-heartbeat-seconds", type=float, default=30.0)
        sub.add_argument("--stale-recovery-seconds", type=float, default=10.0)
        sub.add_argument(
            "--worker-mode",
            choices=["codex", "claude", "grok", "gemini", "stub"],
            default=os.environ.get("CENTRAL_WORKER_MODE", "codex"),
        )
        sub.add_argument("--default-codex-model")
        sub.add_argument("--default-worker-model", "--worker-model")
        sub.add_argument("--audit-worker-model", default=None, help="Separate model for audit tasks")
        sub.add_argument("--notify", action="store_true", default=False)
        sub.add_argument("--remote-workers", action="store_true", default=False, help="Enable remote worker coordination API")
        sub.add_argument("--coordination-port", type=int, default=DEFAULT_COORDINATION_PORT, help=f"Coordination API port (default {DEFAULT_COORDINATION_PORT})")
        sub.add_argument("--max-remote-workers", type=int, default=DEFAULT_MAX_REMOTE_WORKERS, help=f"Max concurrent remote workers (default {DEFAULT_MAX_REMOTE_WORKERS})")
        sub.add_argument("--max-repo-workers", type=int, default=DEFAULT_MAX_REPO_WORKERS, help=f"Max concurrent workers per repo (default {DEFAULT_MAX_REPO_WORKERS})")
        sub.set_defaults(func=func)

    self_check_parser = subparsers.add_parser(
        "self-check", help="Run a stub-mode CENTRAL runtime smoke check"
    )
    self_check_parser.set_defaults(func=command_self_check)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
