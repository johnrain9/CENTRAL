"""Observation and status helpers for central_runtime_v2.

Read-only / query helpers that inspect worker state, log files, and status
caches.  No imports from central_runtime_v2.dispatcher — import direction is
dispatcher → observation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import central_task_db as task_db

from central_runtime_v2.config import ActiveWorker, DispatcherConfig, RuntimePaths  # noqa: F401 – re-exported for callers

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVE_WORKER_RUNTIME_STATUSES: frozenset[str] = frozenset({"claimed", "running"})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Success / validation helpers
# ---------------------------------------------------------------------------


def success_runtime_status(snapshot: dict[str, Any]) -> str:
    if snapshot.get("approval_required"):
        return "pending_review"
    if snapshot.get("task_type") == "truth":
        return "pending_review"
    return "done"


def summarize_validation_results(entries: list[dict[str, Any]]) -> str | None:
    summaries: list[str] = []
    for entry in entries:
        name = str(entry.get("name") or "validation").strip()
        passed = bool(entry.get("passed"))
        notes = str(entry.get("notes") or "").strip()
        status = "passed" if passed else "failed"
        summaries.append(f"{name}: {status}{f' ({notes})' if notes else ''}")
    return "; ".join(summaries) if summaries else None


def add_artifacts(task_id: str, artifacts: list[tuple[str, str, dict[str, Any]]], db_path: Path) -> None:
    conn = task_db.connect(db_path)
    task_db.require_initialized_db(conn, db_path)
    try:
        with conn:
            for kind, path_or_uri, metadata in artifacts:
                task_db.insert_artifact(
                    conn,
                    task_id=task_id,
                    artifact_kind=kind,
                    path_or_uri=path_or_uri,
                    label=Path(path_or_uri).name,
                    metadata=metadata,
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Timestamp / time helpers
# ---------------------------------------------------------------------------


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def age_seconds(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return round((now - value).total_seconds(), 3)


def seconds_until(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return round((value - now).total_seconds(), 3)


def clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------------------
# File / log helpers
# ---------------------------------------------------------------------------


def read_last_line(path: Path, *, max_bytes: int = 4096) -> str:
    if not path.exists() or path.stat().st_size <= 0:
        return ""
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - max_bytes))
        chunk = handle.read().decode("utf-8", errors="replace")
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    return lines[-1][:240] if lines else ""


def file_metadata(path: Path | None, *, now: datetime) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "exists": False,
            "size_bytes": None,
            "modified_at": None,
            "age_seconds": None,
            "last_line_preview": "",
        }
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": None,
            "modified_at": None,
            "age_seconds": None,
            "last_line_preview": "",
        }
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": iso_or_none(modified_at),
        "age_seconds": age_seconds(now, modified_at),
        "last_line_preview": read_last_line(path) if path.suffix == ".log" else "",
    }


# ---------------------------------------------------------------------------
# Status cache helpers
# ---------------------------------------------------------------------------


def load_status_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cache = payload.get("workers") if isinstance(payload, dict) else None
    return cache if isinstance(cache, dict) else {}


def save_status_cache(cache_path: Path, workers: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    trimmed_items = sorted(
        workers.items(),
        key=lambda item: str((item[1] or {}).get("observed_at") or ""),
        reverse=True,
    )[:200]
    payload = {"updated_at": _utc_now(), "workers": {key: value for key, value in trimmed_items}}
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def log_growth_payload(
    cache: dict[str, dict[str, Any]],
    worker_key: str,
    log_info: dict[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous = cache.get(worker_key) or {}
    previous_size = previous.get("size_bytes")
    current_size = log_info.get("size_bytes")
    growth_bytes = None
    if isinstance(previous_size, int) and isinstance(current_size, int):
        growth_bytes = current_size - previous_size
    growth = {
        "bytes_since_last_inspection": growth_bytes,
        "previous_observed_at": previous.get("observed_at"),
        "previous_size_bytes": previous_size if isinstance(previous_size, int) else None,
    }
    updated = {
        "observed_at": observed_at,
        "size_bytes": current_size if isinstance(current_size, int) else None,
        "path": log_info.get("path"),
    }
    return growth, updated


# ---------------------------------------------------------------------------
# Artifact / run-id helpers
# ---------------------------------------------------------------------------


def select_latest_artifact_path(artifacts: list[dict[str, Any]], suffix: str) -> Path | None:
    candidates = [
        artifact
        for artifact in artifacts
        if str(artifact.get("path_or_uri") or "").endswith(suffix)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda artifact: str(artifact.get("created_at") or ""), reverse=True)
    return Path(str(candidates[0]["path_or_uri"]))


def infer_recent_run_id(task_id: str, artifacts: list[dict[str, Any]], paths: RuntimePaths) -> str | None:
    for suffix in (".log", ".json", ".md"):
        path = select_latest_artifact_path(artifacts, suffix)
        if path is not None:
            return path.stem
    logs_dir = paths.worker_logs_dir / task_id
    if not logs_dir.exists():
        return None
    latest_log = max(
        logs_dir.glob("*.log"),
        key=lambda item: (item.stat().st_mtime_ns, item.stat().st_ino, item.name),
        default=None,
    )
    return latest_log.stem if latest_log is not None else None


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def latest_runtime_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if str(event.get("event_type") or "").startswith("runtime."):
            return event
    return None


def latest_heartbeat_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("event_type") == "runtime.heartbeat":
            return event
    return None


def worker_run_paths(
    paths: RuntimePaths,
    task_id: str,
    run_id: str | None,
    artifacts: list[dict[str, Any]],
) -> dict[str, Path | None]:
    if run_id:
        return {
            "prompt": paths.worker_prompts_dir / task_id / f"{run_id}.md",
            "log": paths.worker_logs_dir / task_id / f"{run_id}.log",
            "result": paths.worker_results_dir / task_id / f"{run_id}.json",
        }
    return {
        "prompt": select_latest_artifact_path(artifacts, ".md"),
        "log": select_latest_artifact_path(artifacts, ".log"),
        "result": select_latest_artifact_path(artifacts, ".json"),
    }


# ---------------------------------------------------------------------------
# Worker classification
# ---------------------------------------------------------------------------


def classify_worker_run(
    snapshot: dict[str, Any],
    *,
    heartbeat_age: float | None,
    seconds_to_lease_expiry: float | None,
    log_info: dict[str, Any],
    log_growth: dict[str, Any],
    runtime_event_age: float | None,
    transition_age: float | None,
) -> tuple[str, str]:
    runtime = snapshot.get("runtime") or {}
    status = str(runtime.get("runtime_status") or "none")
    lease = snapshot.get("lease") or {}
    lease_start = parse_timestamp(lease.get("lease_acquired_at"))
    lease_expiry = parse_timestamp(lease.get("lease_expires_at"))
    lease_window = 60.0
    if lease_start is not None and lease_expiry is not None:
        lease_window = max(15.0, (lease_expiry - lease_start).total_seconds())
    recent_window = clamp(lease_window / 2.0, minimum=15.0, maximum=120.0)
    stale_window = clamp(lease_window, minimum=45.0, maximum=600.0)
    log_age = log_info.get("age_seconds")
    log_growth_bytes = log_growth.get("bytes_since_last_inspection")
    has_recent_signal = any(
        [
            isinstance(log_growth_bytes, int) and log_growth_bytes > 0,
            isinstance(log_age, (int, float)) and log_age <= recent_window,
            isinstance(heartbeat_age, (int, float)) and heartbeat_age <= recent_window,
            isinstance(runtime_event_age, (int, float)) and runtime_event_age <= recent_window,
        ]
    )
    if status in ACTIVE_WORKER_RUNTIME_STATUSES:
        if isinstance(seconds_to_lease_expiry, (int, float)) and seconds_to_lease_expiry <= 0:
            return "potentially_stuck", "lease expired before inspection"
        if (
            status == "claimed"
            and not has_recent_signal
            and isinstance(transition_age, (int, float))
            and transition_age > stale_window
        ):
            return "potentially_stuck", "task claimed but worker never showed activity"
        if (
            isinstance(heartbeat_age, (int, float))
            and heartbeat_age > stale_window
            and (not isinstance(log_age, (int, float)) or log_age > stale_window)
        ):
            return "potentially_stuck", "heartbeat and log output both look stale"
        if has_recent_signal:
            return "healthy", "fresh heartbeat or log activity detected"
        return "low_activity", "worker is active but recent progress signals are quiet"
    if status in {"done", "pending_review"}:
        return "recently_finished", "latest run reached a successful terminal state"
    if status in {"failed", "timeout", "canceled"}:
        return "recent_issue", "latest run ended with a non-success terminal state"
    return "idle", "no active worker lease"


def worker_log_signal(
    snapshot: dict[str, Any],
    *,
    log_info: dict[str, Any],
    log_growth: dict[str, Any],
    stale_threshold: float = 60.0,
) -> dict[str, Any]:
    """Return a signal dict describing the log file's growth state."""
    growth_bytes = log_growth.get("bytes_since_last_inspection")
    age = log_info.get("age_seconds")
    is_stale = isinstance(age, (int, float)) and age > stale_threshold
    if isinstance(growth_bytes, int) and growth_bytes > 0:
        state = "growing"
    elif is_stale:
        state = "stale"
    else:
        state = "flat"
    return {"state": state, "stale": is_stale}


# ---------------------------------------------------------------------------
# Status text rendering
# ---------------------------------------------------------------------------


def worker_status_text(payload: dict[str, Any]) -> str:
    def _fmt_seconds(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "-"
        return f"{value:.1f}"

    def _fmt_bytes(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "-"
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f}MB"
        if value >= 1024:
            return f"{value / 1024:.1f}KB"
        return f"{int(value)}B"

    summary = payload["summary"]
    runtime_paths = payload.get("runtime_paths") or {}
    lines = [
        f"Worker status: {summary['overall_status']}",
        summary["headline"],
    ]
    if runtime_paths.get("worker_results_dir"):
        lines.append(f"Structured results: {runtime_paths['worker_results_dir']}")
    lines.append(
        "Active workers: "
        f"{summary['active_count']} | healthy={summary['healthy_count']} "
        f"| low_activity={summary['low_activity_count']} | potentially_stuck={summary['potentially_stuck_count']}"
    )
    if payload["active_workers"]:
        for worker in payload["active_workers"]:
            log = worker.get("log") or {}
            log_size = _fmt_bytes(log.get("size_bytes"))
            growth = log.get("growth") or {}
            growth_bytes = growth.get("bytes_since_last_inspection")
            log_delta = (
                f"+{_fmt_bytes(growth_bytes)}"
                if isinstance(growth_bytes, int) and growth_bytes > 0
                else (f"{_fmt_bytes(growth_bytes)}" if isinstance(growth_bytes, int) else "-")
            )
            log_signal = (log.get("signal") or {}).get("state") or "-"
            lines.append(
                f"- {worker['observed_state']}: {worker['task_id']} run={worker.get('run_id') or '-'} "
                f"runtime={worker['runtime_status']} heartbeat_age={_fmt_seconds((worker.get('heartbeat') or {}).get('age_seconds'))}s "
                f"log_size={log_size} log_delta={log_delta} log_signal={log_signal} "
                f"model={((worker.get('worker') or {}).get('model') or '-')} reason={worker.get('reason', '-')}"
            )
    else:
        lines.append("- no active workers")
    if payload["recent_workers"]:
        lines.append("Recent workers:")
        for worker in payload["recent_workers"]:
            lines.append(
                f"- {worker['observed_state']}: {worker['task_id']} run={worker['run_id'] or '-'} "
                f"runtime={worker['runtime_status']} finished_at={worker['runtime']['finished_at'] or worker['runtime']['last_transition_at']} "
                f"model={((worker.get('worker') or {}).get('model') or '-')} reason={worker['reason']}"
            )
    return "\n".join(lines)
