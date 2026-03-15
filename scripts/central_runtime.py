#!/usr/bin/env python3
"""CENTRAL-native dispatcher daemon and worker execution bridge."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_STATE_DIR = REPO_ROOT / "state" / "central_runtime"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "central_tasks.db"
AUTONOMY_ROOT = Path("/home/cobra/photo_auto_tagging")
AUTONOMY_SCHEMA_PATH = AUTONOMY_ROOT / "autonomy" / "schemas" / "worker_result.schema.json"
AUTONOMY_PROFILE = os.environ.get("AUTONOMY_PROFILE")

if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))

import central_task_db as task_db


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def future_utc(seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


@dataclass(frozen=True)
class RuntimePaths:
    state_dir: Path
    lock_path: Path
    log_path: Path
    worker_status_cache_path: Path
    worker_logs_dir: Path
    worker_results_dir: Path
    worker_prompts_dir: Path
    worker_reports_dir: Path


@dataclass
class ActiveWorker:
    task: dict[str, Any]
    worker_id: str
    run_id: str
    pid: int
    proc: subprocess.Popen[str] | None
    log_handle: Any | None
    prompt_path: Path
    result_path: Path
    log_path: Path
    process_start_token: str | None
    started_at: datetime | None
    start_monotonic: float | None
    last_heartbeat_monotonic: float
    timeout_seconds: int
    adopted: bool = False


@dataclass(frozen=True)
class DispatcherConfig:
    db_path: Path
    state_dir: Path
    max_workers: int
    poll_interval: float
    heartbeat_seconds: float
    status_heartbeat_seconds: float
    stale_recovery_seconds: float
    worker_mode: str


def resolve_state_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_path = os.environ.get("CENTRAL_RUNTIME_STATE_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_STATE_DIR


def build_runtime_paths(state_dir: Path) -> RuntimePaths:
    return RuntimePaths(
        state_dir=state_dir,
        lock_path=state_dir / "dispatcher.lock",
        log_path=state_dir / "dispatcher.log",
        worker_status_cache_path=state_dir / ".worker-status-cache.json",
        worker_logs_dir=state_dir / ".worker-logs",
        worker_results_dir=state_dir / ".worker-results",
        worker_prompts_dir=state_dir / ".worker-prompts",
        worker_reports_dir=state_dir / ".worker-reports",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    for path in [
        paths.state_dir,
        paths.worker_logs_dir,
        paths.worker_results_dir,
        paths.worker_prompts_dir,
        paths.worker_reports_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


ACTIVE_WORKER_RUNTIME_STATUSES = {"claimed", "running"}
RECENT_WORKER_RUNTIME_STATUSES = {"pending_review", "failed", "timeout", "canceled", "done"}
DEFAULT_HANDOFF_LEASE_SECONDS = 90


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_start_token(pid: int | None) -> str | None:
    if not pid or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 1 :].strip().split()
    if len(fields) <= 19:
        return None
    return fields[19]


def process_matches(pid: int | None, expected_start_token: str | None) -> bool:
    if not pid_alive(pid):
        return False
    if not expected_start_token:
        return True
    current = process_start_token(pid)
    return current == expected_start_token if current is not None else False


def terminate_process(pid: int | None, proc: subprocess.Popen[str] | None) -> None:
    if proc is not None:
        try:
            proc.terminate()
            return
        except Exception:
            pass
    if pid and pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def read_lock(lock_path: Path) -> dict[str, Any] | None:
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_lock(lock_path: Path, payload: dict[str, Any]) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def acquire_lock(paths: RuntimePaths, config: DispatcherConfig) -> None:
    payload = read_lock(paths.lock_path)
    if payload is not None:
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            pid = None
        if pid is not None and pid_alive(pid):
            die(f"dispatcher already running with pid={pid}")
        try:
            paths.lock_path.unlink()
        except FileNotFoundError:
            pass
    write_lock(
        paths.lock_path,
        {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "db_path": str(config.db_path),
            "log_path": str(paths.log_path),
            "started_at": utc_now(),
            "max_workers": config.max_workers,
            "poll_interval": config.poll_interval,
            "heartbeat_seconds": config.heartbeat_seconds,
            "status_heartbeat_seconds": config.status_heartbeat_seconds,
            "stale_recovery_seconds": config.stale_recovery_seconds,
            "worker_mode": config.worker_mode,
        },
    )


def release_lock(paths: RuntimePaths) -> None:
    payload = read_lock(paths.lock_path)
    if payload is None:
        return
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        pid = None
    if pid is None or pid == os.getpid() or not pid_alive(pid):
        try:
            paths.lock_path.unlink()
        except FileNotFoundError:
            pass


class DaemonLog:
    def __init__(self, paths: RuntimePaths):
        self.path = paths.log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, level: str, subsystem: str, message: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {level} [{subsystem}] {message}"
        print(line)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def tail(self, lines: int = 120) -> str:
        if not self.path.exists():
            return ""
        data = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:])


def connect_initialized(db_path: Path):
    conn = task_db.connect(db_path)
    task_db.require_initialized_db(conn, db_path)
    return conn


def derive_worker_id(task_id: str) -> str:
    suffix = int(time.time() * 1000)
    return f"central-worker:{socket.gethostname()}:{os.getpid()}:{task_id}:{suffix}"


def extract_markdown_items(text: str) -> list[str]:
    items: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line[:2].isdigit() and ". " in line:
            items.append(line.split(". ", 1)[1].strip())
    return [item for item in items if item]


def build_worker_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    execution = snapshot.get("execution") or {}
    metadata = snapshot.get("metadata") or {}
    execution_metadata = execution.get("metadata") or {}
    deliverables = extract_markdown_items(snapshot.get("deliverables_md", "")) or [snapshot.get("deliverables_md", "").strip()]
    scope_notes = extract_markdown_items(snapshot.get("scope_md", "")) or [snapshot.get("scope_md", "").strip()]
    validation_commands = extract_markdown_items(snapshot.get("testing_md", "")) or [snapshot.get("testing_md", "").strip()]
    validation_commands = [item for item in validation_commands if item]
    deliverables = [item for item in deliverables if item]
    scope_notes = [item for item in scope_notes if item]
    prompt_sections = [
        f"## Objective\n{snapshot.get('objective_md', '').strip()}",
        f"## Context\n{snapshot.get('context_md', '').strip()}",
        f"## Scope\n{snapshot.get('scope_md', '').strip()}",
        f"## Deliverables\n{snapshot.get('deliverables_md', '').strip()}",
        f"## Acceptance\n{snapshot.get('acceptance_md', '').strip()}",
        f"## Testing\n{snapshot.get('testing_md', '').strip()}",
        f"## Dispatch Contract\n{snapshot.get('dispatch_md', '').strip()}",
        f"## Closeout Contract\n{snapshot.get('closeout_md', '').strip()}",
        f"## Reconciliation\n{snapshot.get('reconciliation_md', '').strip()}",
    ]
    task_category = snapshot.get("task_type") or "implementation"
    if task_category not in {"implementation", "truth"}:
        task_category = "infrastructure"
    return {
        "id": snapshot["task_id"],
        "title": snapshot["title"],
        "category": task_category,
        "task_kind": execution.get("task_kind") or "mutating",
        "repo_root": snapshot["target_repo_root"],
        "prompt_body": "\n\n".join(section for section in prompt_sections if section.strip()),
        "deliverables_json": json.dumps(deliverables),
        "scope_notes_json": json.dumps(scope_notes),
        "validation_commands_json": json.dumps(validation_commands),
        "design_doc_path": metadata.get("design_doc_path"),
        "codex_profile": execution_metadata.get("codex_profile") or AUTONOMY_PROFILE,
        "codex_model": execution_metadata.get("codex_model"),
        "sandbox_mode": execution.get("sandbox_mode"),
        "approval_policy": execution.get("approval_policy"),
        "additional_writable_dirs_json": json.dumps(execution.get("additional_writable_dirs") or []),
    }


def load_autonomy_runner():
    if str(AUTONOMY_ROOT) not in sys.path:
        sys.path.insert(0, str(AUTONOMY_ROOT))
    from autonomy import runner as autonomy_runner  # type: ignore

    return autonomy_runner


def build_stub_command(snapshot: dict[str, Any], run_id: str, result_path: Path) -> list[str]:
    execution_metadata = (snapshot.get("execution") or {}).get("metadata") or {}
    sleep_seconds = float(execution_metadata.get("stub_sleep_seconds", os.environ.get("CENTRAL_STUB_WORKER_SECONDS", "0.5")) or 0.5)
    log_interval_seconds = float(
        execution_metadata.get("stub_log_interval_seconds", min(max(sleep_seconds / 4.0, 0.2), 1.0)) or 0.2
    )
    code = (
        "import json,sys,time;"
        "from pathlib import Path;"
        "task_id,run_id,result_path,sleep_seconds,log_interval=sys.argv[1:6];"
        "sleep_seconds=float(sleep_seconds);"
        "log_interval=max(float(log_interval),0.05);"
        "steps=max(1,int(sleep_seconds/log_interval));"
        "remaining=max(0.0,sleep_seconds-(steps*log_interval));"
        "print(f'stub worker starting task={task_id} run={run_id}', flush=True);"
        "[(print(f'stub progress {index+1}/{steps}', flush=True), time.sleep(log_interval)) for index in range(steps)];"
        "time.sleep(remaining);"
        "payload={"
        "'status':'COMPLETED',"
        "'schema_version':1,"
        "'task_id':task_id,"
        "'run_id':run_id,"
        "'summary':'stub worker completed',"
        "'completed_items':['stub execution completed'],"
        "'remaining_items':[],"
        "'decisions':['used stub worker mode'],"
        "'discoveries':[],"
        "'blockers':[],"
        "'validation':[{'name':'stub-mode','passed':True,'notes':'synthetic worker result'}],"
        "'files_changed':[],"
        "'warnings':[],"
        "'artifacts':[]"
        "};"
        "Path(result_path).write_text(json.dumps(payload), encoding='utf-8')"
    )
    return [
        sys.executable,
        "-c",
        code,
        snapshot["task_id"],
        run_id,
        str(result_path),
        str(sleep_seconds),
        str(log_interval_seconds),
    ]


def success_runtime_status(snapshot: dict[str, Any]) -> str:
    if snapshot.get("approval_required"):
        return "pending_review"
    if snapshot.get("task_type") == "truth":
        return "pending_review"
    return "done"


def add_artifacts(task_id: str, artifacts: list[tuple[str, str, dict[str, Any]]], db_path: Path) -> None:
    conn = connect_initialized(db_path)
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
    payload = {"updated_at": utc_now(), "workers": {key: value for key, value in trimmed_items}}
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
    latest_log = max(logs_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, default=None)
    return latest_log.stem if latest_log is not None else None


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


def worker_run_paths(paths: RuntimePaths, task_id: str, run_id: str | None, artifacts: list[dict[str, Any]]) -> dict[str, Path | None]:
    if run_id:
        return {
            "prompt": paths.worker_prompts_dir / task_id / f"{run_id}.md",
            "log": paths.worker_logs_dir / task_id / f"{run_id}.log",
            "result": paths.worker_results_dir / task_id / f"{run_id}.json",
            "report": paths.worker_reports_dir / task_id / f"{run_id}.md",
        }
    return {
        "prompt": select_latest_artifact_path(artifacts, ".md"),
        "log": select_latest_artifact_path(artifacts, ".log"),
        "result": select_latest_artifact_path(artifacts, ".json"),
        "report": select_latest_artifact_path(artifacts, ".report"),
    }


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
        if status == "claimed" and not has_recent_signal and isinstance(transition_age, (int, float)) and transition_age > stale_window:
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


def worker_status_text(payload: dict[str, Any]) -> str:
    def _fmt_seconds(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "-"
        return f"{value:.1f}"

    summary = payload["summary"]
    lines = [
        f"Worker status: {summary['overall_status']}",
        summary["headline"],
        (
            "Active workers: "
            f"{summary['active_count']} | healthy={summary['healthy_count']} "
            f"| low_activity={summary['low_activity_count']} | potentially_stuck={summary['potentially_stuck_count']}"
        ),
    ]
    if payload["active_workers"]:
        for worker in payload["active_workers"]:
            lines.append(
                (
                    f"- {worker['observed_state']}: {worker['task_id']} run={worker['run_id'] or '-'} "
                    f"runtime={worker['runtime_status']} heartbeat_age={_fmt_seconds(worker['heartbeat']['age_seconds'])}s "
                    f"log_age={_fmt_seconds(worker['log']['age_seconds'])}s reason={worker['reason']}"
                )
            )
    else:
        lines.append("- no active workers")
    if payload["recent_workers"]:
        lines.append("Recent workers:")
        for worker in payload["recent_workers"]:
            lines.append(
                (
                    f"- {worker['observed_state']}: {worker['task_id']} run={worker['run_id'] or '-'} "
                    f"runtime={worker['runtime_status']} finished_at={worker['runtime']['finished_at'] or worker['runtime']['last_transition_at']} "
                    f"reason={worker['reason']}"
                )
            )
    return "\n".join(lines)


class CentralDispatcher:
    def __init__(self, config: DispatcherConfig):
        self.config = config
        self.paths = build_runtime_paths(config.state_dir)
        ensure_runtime_dirs(self.paths)
        self.logger = DaemonLog(self.paths)
        self._running = False
        self._stop_requested = False
        self._force_stop = False
        self._active: dict[str, ActiveWorker] = {}
        self._last_recovery_monotonic = 0.0
        self._last_status_heartbeat_monotonic = 0.0

    def _signal_handler(self, _signum: int, _frame: Any) -> None:
        if not self._stop_requested:
            self._stop_requested = True
            self.logger.emit("INF", "central.dispatcher", "dispatcher_stopping")
        else:
            self._force_stop = True
            self.logger.emit("INF", "central.dispatcher", "dispatcher_force_stop_requested")

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _connect(self):
        return connect_initialized(self.config.db_path)

    def _lease_seconds(self) -> int:
        return max(5, int(self.config.heartbeat_seconds * 3))

    def _worker_elapsed_seconds(self, state: ActiveWorker) -> float:
        if state.start_monotonic is not None:
            return max(0.0, time.monotonic() - state.start_monotonic)
        if state.started_at is not None:
            return max(0.0, (datetime.now(timezone.utc) - state.started_at).total_seconds())
        return 0.0

    def _worker_is_running(self, state: ActiveWorker) -> bool:
        if state.proc is not None and state.proc.poll() is not None:
            return False
        return process_matches(state.pid, state.process_start_token)

    def _close_worker_state(self, state: ActiveWorker) -> None:
        if state.log_handle is not None:
            try:
                state.log_handle.close()
            except Exception:
                pass
            state.log_handle = None

    def _supervision_payload(self, state: ActiveWorker) -> dict[str, Any]:
        return {
            "run_id": state.run_id,
            "worker_pid": state.pid,
            "worker_process_start_token": state.process_start_token,
            "worker_mode": self.config.worker_mode,
            "prompt_path": str(state.prompt_path),
            "result_path": str(state.result_path),
            "log_path": str(state.log_path),
            "repo_root": str(state.task["target_repo_root"]),
            "timeout_seconds": state.timeout_seconds,
            "started_at": iso_or_none(state.started_at),
            "dispatcher_pid": os.getpid(),
            "dispatcher_host": socket.gethostname(),
            "updated_at": utc_now(),
            "adopted": state.adopted,
        }

    def _sync_worker_lease(
        self,
        state: ActiveWorker,
        *,
        lease_seconds: int | None = None,
        metadata_updates: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
        actor_id: str = "central.dispatcher",
    ) -> None:
        conn = self._connect()
        try:
            task_db.begin_immediate(conn)
            lease = task_db.fetch_active_lease(conn, state.task["task_id"])
            if lease is None:
                conn.rollback()
                raise RuntimeError(f"no active lease for {state.task['task_id']}")
            if str(lease["lease_owner_id"]) != state.worker_id:
                conn.rollback()
                raise RuntimeError(
                    f"lease owner mismatch for {state.task['task_id']}: expected {lease['lease_owner_id']}, got {state.worker_id}"
                )
            metadata = task_db.parse_json_text(lease["lease_metadata_json"], default={})
            set_clauses: list[str] = []
            params: list[Any] = []
            if metadata_updates is not None:
                metadata.update(metadata_updates)
                set_clauses.append("lease_metadata_json = ?")
                params.append(task_db.compact_json(metadata))
            heartbeat_at = None
            lease_expires_at = None
            if lease_seconds is not None:
                heartbeat_at = utc_now()
                lease_expires_at = future_utc(lease_seconds)
                set_clauses.extend(["lease_expires_at = ?", "last_heartbeat_at = ?"])
                params.extend([lease_expires_at, heartbeat_at])
            if set_clauses:
                conn.execute(
                    f"UPDATE task_active_leases SET {', '.join(set_clauses)} WHERE task_id = ? AND lease_owner_id = ?",
                    (*params, state.task["task_id"], state.worker_id),
                )
            if event_type:
                task_db.insert_event(
                    conn,
                    task_id=state.task["task_id"],
                    event_type=event_type,
                    actor_kind="runtime",
                    actor_id=actor_id,
                    payload=event_payload or {},
                )
            conn.commit()
        finally:
            conn.close()
        lease_payload = state.task.setdefault("lease", {})
        if metadata_updates is not None:
            current_metadata = lease_payload.get("metadata") or {}
            if isinstance(current_metadata, dict):
                current_metadata.update(metadata_updates)
                lease_payload["metadata"] = current_metadata
        if lease_seconds is not None and heartbeat_at is not None and lease_expires_at is not None:
            lease_payload["last_heartbeat_at"] = heartbeat_at
            lease_payload["lease_expires_at"] = lease_expires_at
            state.last_heartbeat_monotonic = time.monotonic()

    def _persist_worker_supervision(
        self,
        state: ActiveWorker,
        *,
        handoff: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        metadata_updates = {"supervision": self._supervision_payload(state)}
        if handoff is not None:
            metadata_updates["handoff"] = handoff
        self._sync_worker_lease(
            state,
            metadata_updates=metadata_updates,
            event_type=event_type,
            event_payload=event_payload,
        )

    def _heartbeat_worker(self, state: ActiveWorker, *, lease_seconds: int | None = None, actor_id: str = "central.dispatcher") -> None:
        self._sync_worker_lease(state, lease_seconds=lease_seconds or self._lease_seconds(), actor_id=actor_id)

    def _handoff_lease_seconds(self, state: ActiveWorker) -> int:
        lease_metadata = ((state.task.get("lease") or {}).get("metadata") or {})
        try:
            current_lease_seconds = int(lease_metadata.get("lease_seconds") or self._lease_seconds())
        except (TypeError, ValueError):
            current_lease_seconds = self._lease_seconds()
        return max(current_lease_seconds, DEFAULT_HANDOFF_LEASE_SECONDS)

    def _dispatcher_snapshot(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            snapshots = task_db.fetch_task_snapshots(conn)
            eligible = task_db.order_eligible_snapshots(snapshots)
            runtime_summary = conn.execute(
                "SELECT runtime_status, COUNT(*) AS c FROM task_runtime_state GROUP BY runtime_status"
            ).fetchall()
            active_leases = int(conn.execute("SELECT COUNT(*) AS c FROM task_active_leases").fetchone()["c"])
            counts = {str(row["runtime_status"]): int(row["c"]) for row in runtime_summary}
        finally:
            conn.close()
        return {
            "eligible_count": len(eligible),
            "next_eligible_task_id": eligible[0]["task_id"] if eligible else None,
            "runtime_counts": counts,
            "active_leases": active_leases,
        }

    @staticmethod
    def _format_task_ids(task_ids: list[str], limit: int = 5) -> str:
        if not task_ids:
            return "-"
        if len(task_ids) <= limit:
            return ",".join(task_ids)
        shown = ",".join(task_ids[:limit])
        remaining = len(task_ids) - limit
        return f"{shown},+{remaining}_more"

    def _emit_status_heartbeat(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_heartbeat_monotonic < self.config.status_heartbeat_seconds:
            return
        snapshot = self._dispatcher_snapshot()
        active_ids = sorted(self._active)
        self.logger.emit(
            "INF",
            "central.dispatcher",
            (
                "heartbeat "
                f"state={'stopping' if self._stop_requested else 'running'} "
                f"workers={len(active_ids)}/{self.config.max_workers} "
                f"running_tasks={self._format_task_ids(active_ids)} "
                f"eligible={snapshot['eligible_count']} "
                f"next={snapshot['next_eligible_task_id'] or '-'} "
                f"leases={snapshot['active_leases']} "
                f"review={snapshot['runtime_counts'].get('pending_review', 0)} "
                f"failed={snapshot['runtime_counts'].get('failed', 0)}"
            ),
        )
        self._last_status_heartbeat_monotonic = now

    def _claim_next(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            try:
                return task_db.runtime_claim(
                    conn,
                    worker_id=derive_worker_id("slot"),
                    queue_name="default",
                    lease_seconds=max(5, int(self.config.heartbeat_seconds * 3)),
                    task_id=None,
                    actor_id="central.dispatcher",
                )
            except SystemExit as exc:
                if exc.code == 1:
                    return None
                raise
        finally:
            conn.close()

    def _spawn_worker(self, snapshot: dict[str, Any]) -> None:
        worker_task = build_worker_task(snapshot)
        run_id = (snapshot.get("lease") or {}).get("execution_run_id") or f"{snapshot['task_id']}-{int(time.time())}"
        prompts_dir = self.paths.worker_prompts_dir / snapshot["task_id"]
        results_dir = self.paths.worker_results_dir / snapshot["task_id"]
        logs_dir = self.paths.worker_logs_dir / snapshot["task_id"]
        prompts_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompts_dir / f"{run_id}.md"
        result_path = results_dir / f"{run_id}.json"
        log_path = logs_dir / f"{run_id}.log"

        dependency_context = [
            {
                "task_id": dep.get("depends_on_task_id"),
                "title": dep.get("depends_on_title"),
                "summary": dep.get("depends_on_status"),
                "decisions": "",
                "blockers": "",
                "validation": "",
            }
            for dep in snapshot.get("dependencies") or []
        ]

        if self.config.worker_mode == "codex":
            worker_task["run_id"] = run_id
            autonomy_runner = load_autonomy_runner()
            prompt_text = autonomy_runner.build_prompt(
                worker_task,
                autonomy_runner.normalize_dependency_context(dependency_context),
            )
            command = autonomy_runner.build_codex_command(worker_task, result_path, AUTONOMY_SCHEMA_PATH)
            stdin_mode: Any = subprocess.PIPE
        else:
            prompt_text = worker_task["prompt_body"]
            command = build_stub_command(snapshot, run_id, result_path)
            stdin_mode = subprocess.DEVNULL

        prompt_path.write_text(prompt_text, encoding="utf-8")
        log_handle = log_path.open("a", encoding="utf-8")

        conn = self._connect()
        try:
            with conn:
                task_db.runtime_transition(
                    conn,
                    task_id=snapshot["task_id"],
                    status="running",
                    worker_id=(snapshot.get("lease") or {}).get("lease_owner_id"),
                    error_text=None,
                    notes=f"worker_mode={self.config.worker_mode}",
                    artifacts=[],
                    actor_id="central.dispatcher",
                )
        finally:
            conn.close()

        proc = subprocess.Popen(
            command,
            cwd=worker_task["repo_root"],
            stdin=stdin_mode,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if stdin_mode == subprocess.PIPE and proc.stdin is not None:
            proc.stdin.write(prompt_text)
            proc.stdin.close()

        started_at = datetime.now(timezone.utc).replace(microsecond=0)
        state = ActiveWorker(
            task=snapshot,
            worker_id=(snapshot.get("lease") or {}).get("lease_owner_id") or derive_worker_id(snapshot["task_id"]),
            run_id=run_id,
            pid=proc.pid,
            proc=proc,
            log_handle=log_handle,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=process_start_token(proc.pid),
            started_at=started_at,
            start_monotonic=time.monotonic(),
            last_heartbeat_monotonic=time.monotonic(),
            timeout_seconds=int((snapshot.get("execution") or {}).get("timeout_seconds") or 1800),
        )
        try:
            self._persist_worker_supervision(state, handoff={})
        except Exception:
            terminate_process(state.pid, state.proc)
            self._close_worker_state(state)
            raise

        self.logger.emit(
            "INF",
            "central.dispatcher",
            f"worker_spawned task={snapshot['task_id']} run={run_id} pid={proc.pid} mode={self.config.worker_mode}",
        )
        self._active[snapshot["task_id"]] = state
        self._emit_status_heartbeat(force=True)

    def _adopt_active_workers(self) -> int:
        conn = self._connect()
        try:
            snapshots = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        adopted = 0
        for snapshot in snapshots:
            runtime = snapshot.get("runtime") or {}
            lease = snapshot.get("lease") or {}
            if snapshot["task_id"] in self._active:
                continue
            if str(runtime.get("runtime_status") or "") not in ACTIVE_WORKER_RUNTIME_STATUSES:
                continue
            if not lease:
                continue
            metadata = lease.get("metadata") or {}
            supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
            if not isinstance(supervision, dict):
                continue
            run_id = str(supervision.get("run_id") or lease.get("execution_run_id") or "")
            worker_pid = supervision.get("worker_pid")
            try:
                pid = int(worker_pid)
            except (TypeError, ValueError):
                continue
            prompt_path = Path(str(supervision.get("prompt_path") or self.paths.worker_prompts_dir / snapshot["task_id"] / f"{run_id}.md"))
            result_path = Path(str(supervision.get("result_path") or self.paths.worker_results_dir / snapshot["task_id"] / f"{run_id}.json"))
            log_path = Path(str(supervision.get("log_path") or self.paths.worker_logs_dir / snapshot["task_id"] / f"{run_id}.log"))
            try:
                timeout_seconds = int(supervision.get("timeout_seconds") or (snapshot.get("execution") or {}).get("timeout_seconds") or 1800)
            except (TypeError, ValueError):
                timeout_seconds = int((snapshot.get("execution") or {}).get("timeout_seconds") or 1800)
            state = ActiveWorker(
                task=snapshot,
                worker_id=str(lease.get("lease_owner_id") or runtime.get("claimed_by") or ""),
                run_id=run_id,
                pid=pid,
                proc=None,
                log_handle=None,
                prompt_path=prompt_path,
                result_path=result_path,
                log_path=log_path,
                process_start_token=str(supervision.get("worker_process_start_token") or "") or None,
                started_at=parse_timestamp(str(supervision.get("started_at") or runtime.get("started_at") or runtime.get("claimed_at") or "")),
                start_monotonic=None,
                last_heartbeat_monotonic=0.0,
                timeout_seconds=timeout_seconds,
                adopted=True,
            )
            if not process_matches(state.pid, state.process_start_token):
                if state.result_path.exists() or state.log_path.exists() or state.prompt_path.exists():
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_reconcile_post_restart task={snapshot['task_id']} run={state.run_id} pid={state.pid}",
                    )
                    self._finalize_worker(state, timed_out=False)
                continue
            self._persist_worker_supervision(
                state,
                handoff={
                    "state": "adopted",
                    "adopted_at": utc_now(),
                    "adopted_by_dispatcher_pid": os.getpid(),
                },
                event_type="runtime.worker_adopted",
                event_payload={"run_id": state.run_id, "worker_id": state.worker_id, "worker_pid": state.pid},
            )
            self._heartbeat_worker(state, actor_id="central.dispatcher.adopt")
            self._active[snapshot["task_id"]] = state
            adopted += 1
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"worker_adopted task={snapshot['task_id']} run={state.run_id} pid={state.pid}",
            )
        return adopted

    def _prepare_handoff(self) -> None:
        if not self._active:
            return
        handed_off = 0
        for state in list(self._active.values()):
            handoff_lease_seconds = self._handoff_lease_seconds(state)
            handoff = {
                "state": "pending_adoption",
                "requested_at": utc_now(),
                "requested_by_dispatcher_pid": os.getpid(),
                "grace_expires_at": future_utc(handoff_lease_seconds),
            }
            try:
                self._persist_worker_supervision(
                    state,
                    handoff=handoff,
                    event_type="runtime.dispatcher_handoff_requested",
                    event_payload={
                        "run_id": state.run_id,
                        "worker_id": state.worker_id,
                        "worker_pid": state.pid,
                        "grace_expires_at": handoff["grace_expires_at"],
                    },
                )
                self._heartbeat_worker(
                    state,
                    lease_seconds=handoff_lease_seconds,
                    actor_id="central.dispatcher.handoff",
                )
                handed_off += 1
            except Exception as exc:
                self.logger.emit(
                    "INF",
                    "central.dispatcher",
                    f"worker_handoff_error task={state.task['task_id']} run={state.run_id} error={exc}",
                )
        self.logger.emit(
            "INF",
            "central.dispatcher",
            f"dispatcher_handoff_prepared active_workers={handed_off}",
        )

    def _finalize_worker(self, state: ActiveWorker, *, timed_out: bool = False) -> None:
        task_id = state.task["task_id"]
        terminal_artifacts = [str(state.prompt_path), str(state.log_path)]
        conn = self._connect()
        try:
            if timed_out:
                with conn:
                    task_db.runtime_transition(
                        conn,
                        task_id=task_id,
                        status="timeout",
                        worker_id=state.worker_id,
                        error_text="worker timeout",
                        notes="process exceeded timeout_seconds",
                        artifacts=terminal_artifacts,
                        actor_id="central.dispatcher",
                    )
                self.logger.emit("INF", "central.dispatcher", f"worker_timeout task={task_id} run={state.run_id}")
                return

            runtime_status = "failed"
            error_text = None
            notes = None
            extra_artifacts: list[tuple[str, str, dict[str, Any]]] = []
            result_artifacts = terminal_artifacts.copy()
            if state.result_path.exists():
                result_artifacts.append(str(state.result_path))
                try:
                    autonomy_runner = load_autonomy_runner()
                    result = autonomy_runner.load_result_file(state.result_path, task_id=task_id, run_id=state.run_id)
                    runtime_status = success_runtime_status(state.task) if result.status == "COMPLETED" else "failed"
                    notes = result.summary
                    error_text = None if runtime_status in {"done", "pending_review"} else result.summary
                    for artifact in result.artifacts:
                        artifact_path = str(artifact.get("path") or "").strip()
                        if artifact_path:
                            extra_artifacts.append(
                                (
                                    f"worker_{str(artifact.get('type') or 'artifact')}",
                                    artifact_path,
                                    {"notes": artifact.get("notes") or "", "run_id": state.run_id},
                                )
                            )
                except Exception as exc:
                    runtime_status = "failed"
                    error_text = f"result parse failed: {exc}"
            else:
                runtime_status = "failed"
                error_text = "worker exited without result file"

            with conn:
                task_db.runtime_transition(
                    conn,
                    task_id=task_id,
                    status=runtime_status,
                    worker_id=state.worker_id,
                    error_text=error_text,
                    notes=notes,
                    artifacts=result_artifacts,
                    actor_id="central.dispatcher",
                )
            if extra_artifacts:
                add_artifacts(task_id, extra_artifacts, self.config.db_path)
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"worker_finished task={task_id} run={state.run_id} runtime_status={runtime_status}",
            )
        finally:
            conn.close()

    def _process_active(self) -> None:
        for task_id, state in list(self._active.items()):
            elapsed = self._worker_elapsed_seconds(state)
            if elapsed > state.timeout_seconds:
                terminate_process(state.pid, state.proc)
                self._finalize_worker(state, timed_out=True)
                self._close_worker_state(state)
                self._active.pop(task_id, None)
                self._emit_status_heartbeat(force=True)
                continue

            if time.monotonic() - state.last_heartbeat_monotonic >= self.config.heartbeat_seconds:
                try:
                    self._heartbeat_worker(state)
                except Exception as exc:
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_heartbeat_error task={task_id} run={state.run_id} error={exc}",
                    )

            if not self._worker_is_running(state):
                self._finalize_worker(state, timed_out=False)
                self._close_worker_state(state)
                self._active.pop(task_id, None)
                self._emit_status_heartbeat(force=True)

    def _run_stale_recovery(self) -> None:
        if time.monotonic() - self._last_recovery_monotonic < self.config.stale_recovery_seconds:
            return
        conn = self._connect()
        try:
            with conn:
                result = task_db.runtime_recover_stale(conn, limit=50, actor_id="central.dispatcher")
        finally:
            conn.close()
        self._last_recovery_monotonic = time.monotonic()
        if result.get("recovered_count"):
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"stale_recovery recovered={result['recovered_count']}",
            )

    def _fill_workers(self) -> None:
        while len(self._active) < self.config.max_workers and not self._stop_requested:
            snapshot = self._claim_next()
            if snapshot is None:
                break
            try:
                self._spawn_worker(snapshot)
            except Exception as exc:
                self.logger.emit("INF", "central.dispatcher", f"worker_spawn_error task={snapshot['task_id']} error={exc}")
                conn = self._connect()
                try:
                    with conn:
                        task_db.runtime_transition(
                            conn,
                            task_id=snapshot["task_id"],
                            status="failed",
                            worker_id=(snapshot.get("lease") or {}).get("lease_owner_id"),
                            error_text=f"spawn error: {exc}",
                            notes=None,
                            artifacts=[],
                            actor_id="central.dispatcher",
                        )
                finally:
                    conn.close()

    def run_once(self, *, emit_result: bool = True) -> int:
        self._run_stale_recovery()
        snapshot = self._claim_next()
        if snapshot is None:
            if emit_result:
                print(json_dumps({"claimed": False, "reason": "no_eligible_task"}))
            return 0
        self._spawn_worker(snapshot)
        while self._active:
            self._process_active()
            time.sleep(max(0.2, self.config.poll_interval))
        if emit_result:
            print(json_dumps({"claimed": True, "task_id": snapshot["task_id"]}))
        return 0

    def run_daemon(self) -> int:
        acquire_lock(self.paths, self.config)
        self._setup_signals()
        self._running = True
        adopted = self._adopt_active_workers()
        self.logger.emit(
            "INF",
            "central.dispatcher",
            (
                f"dispatcher_started max_workers={self.config.max_workers} "
                f"worker_mode={self.config.worker_mode} adopted_workers={adopted}"
            ),
        )
        self._emit_status_heartbeat(force=True)
        try:
            while True:
                self._process_active()
                if self._force_stop:
                    for state in self._active.values():
                        terminate_process(state.pid, state.proc)
                    break
                if self._stop_requested:
                    self._prepare_handoff()
                    break
                self._run_stale_recovery()
                if not self._stop_requested:
                    self._fill_workers()
                self._emit_status_heartbeat()
                time.sleep(max(0.2, self.config.poll_interval))
        finally:
            for state in list(self._active.values()):
                self._close_worker_state(state)
            release_lock(self.paths)
            self.logger.emit("INF", "central.dispatcher", "dispatcher_stopped")
        return 0


def status_payload(db_path: Path, paths: RuntimePaths) -> dict[str, Any]:
    payload = read_lock(paths.lock_path)
    pid = None
    configured_max_workers = None
    worker_mode = None
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
        "configured_max_workers": configured_max_workers,
        "worker_mode": worker_mode,
        "active_leases": active_leases,
        "eligible_count": eligible,
        "runtime_counts": runtime_counts,
        "lock_payload": payload,
    }


def worker_status_payload(
    db_path: Path,
    paths: RuntimePaths,
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
    if args.follow:
        os.execvp("tail", ["tail", "-n", str(args.lines), "-f", str(paths.log_path)])
    log = DaemonLog(paths)
    print(log.tail(lines=args.lines))
    return 0


def build_dispatcher_config(args: argparse.Namespace) -> DispatcherConfig:
    return DispatcherConfig(
        db_path=task_db.resolve_db_path(args.db_path),
        state_dir=resolve_state_dir(args.state_dir),
        max_workers=args.max_workers,
        poll_interval=args.poll_interval,
        heartbeat_seconds=args.heartbeat_seconds,
        status_heartbeat_seconds=args.status_heartbeat_seconds,
        stale_recovery_seconds=args.stale_recovery_seconds,
        worker_mode=args.worker_mode,
    )


def command_run_once(args: argparse.Namespace) -> int:
    dispatcher = CentralDispatcher(build_dispatcher_config(args))
    return dispatcher.run_once(emit_result=True)


def command_daemon(args: argparse.Namespace) -> int:
    dispatcher = CentralDispatcher(build_dispatcher_config(args))
    return dispatcher.run_daemon()


def smoke_task_payload() -> dict[str, Any]:
    return {
        "task_id": "CENTRAL-RUNTIME-SMOKE",
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
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": {"smoke": True},
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
        )
        dispatcher = CentralDispatcher(cfg)
        dispatcher.run_once(emit_result=False)
        conn = connect_initialized(db_path)
        try:
            task_row = conn.execute(
                "SELECT runtime_status, last_runtime_error FROM task_runtime_state WHERE task_id = ?",
                ("CENTRAL-RUNTIME-SMOKE",),
            ).fetchone()
            payload = {
                "db_path": str(db_path),
                "state_dir": str(state_dir),
                "runtime_status": task_row["runtime_status"] if task_row else None,
                "last_runtime_error": task_row["last_runtime_error"] if task_row else None,
            }
        finally:
            conn.close()
    print(json_dumps(payload))
    return 0


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
        sub.add_argument("--worker-mode", choices=["codex", "stub"], default=os.environ.get("CENTRAL_WORKER_MODE", "codex"))
        sub.set_defaults(func=func)

    self_check_parser = subparsers.add_parser("self-check", help="Run a stub-mode CENTRAL runtime smoke check")
    self_check_parser.set_defaults(func=command_self_check)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
