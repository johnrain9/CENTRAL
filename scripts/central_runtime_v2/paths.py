"""Path resolution, directory management, and lock helpers for central_runtime_v2.

Stdlib-only imports (plus central_runtime_v2.config).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from central_runtime_v2.config import DEFAULT_STATE_DIR, DispatcherConfig, RuntimePaths

# ---------------------------------------------------------------------------
# Small local helpers (no cross-module dep)
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


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
    )


# ---------------------------------------------------------------------------
# Directory management
# ---------------------------------------------------------------------------


def cleanup_legacy_runtime_dirs(paths: RuntimePaths) -> None:
    legacy_reports_dir = paths.state_dir / ".worker-reports"
    if (
        legacy_reports_dir.exists()
        and legacy_reports_dir.is_dir()
        and not legacy_reports_dir.is_symlink()
    ):
        try:
            legacy_reports_dir.rmdir()
        except OSError:
            pass


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    cleanup_legacy_runtime_dirs(paths)
    for path in [
        paths.state_dir,
        paths.worker_logs_dir,
        paths.worker_results_dir,
        paths.worker_prompts_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def runtime_paths_payload(paths: RuntimePaths) -> dict[str, str]:
    return {
        "state_dir": str(paths.state_dir),
        "lock_path": str(paths.lock_path),
        "log_path": str(paths.log_path),
        "worker_logs_dir": str(paths.worker_logs_dir),
        "worker_prompts_dir": str(paths.worker_prompts_dir),
        "worker_results_dir": str(paths.worker_results_dir),
    }


# ---------------------------------------------------------------------------
# Lock file helpers
# ---------------------------------------------------------------------------


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
            "default_worker_model": config.default_worker_model,
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
