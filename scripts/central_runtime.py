#!/usr/bin/env python3
"""CENTRAL-native dispatcher daemon and worker execution bridge."""

from __future__ import annotations

import abc
import argparse
import json
import os
import re
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
AUTONOMY_ROOT = Path(os.environ.get("CENTRAL_AUTONOMY_ROOT", str(REPO_ROOT.parent / "Dispatcher")))  # override via CENTRAL_AUTONOMY_ROOT; default: ../Dispatcher
AUTONOMY_SCHEMA_PATH = AUTONOMY_ROOT / "autonomy" / "schemas" / "worker_result.schema.json"
AUTONOMY_PROFILE = os.environ.get("AUTONOMY_PROFILE")
DEFAULT_CODEX_MODEL = "gpt-5-codex"
DEFAULT_CODEX_MODEL_ENV = "CENTRAL_DISPATCHER_CODEX_MODEL"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_CLAUDE_MODEL_ENV = "CENTRAL_DISPATCHER_CLAUDE_MODEL"
DEFAULT_WORKER_MODEL_ENV = "CENTRAL_DISPATCHER_WORKER_MODEL"

# Model policy tiers.
# High tier is used for design/architecture tasks; medium is the routine default.
# These can be overridden via environment variables.
HIGH_TIER_CLAUDE_MODEL = os.environ.get("CENTRAL_DISPATCHER_HIGH_TIER_CLAUDE_MODEL", "claude-opus-4-6")
HIGH_TIER_CODEX_MODEL = os.environ.get("CENTRAL_DISPATCHER_HIGH_TIER_CODEX_MODEL", "o3")
MEDIUM_TIER_CLAUDE_MODEL = os.environ.get("CENTRAL_DISPATCHER_MEDIUM_TIER_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
MEDIUM_TIER_CODEX_MODEL = os.environ.get("CENTRAL_DISPATCHER_MEDIUM_TIER_CODEX_MODEL", DEFAULT_CODEX_MODEL)

# Task classes that trigger high-tier model selection.
HIGH_TIER_TAGS: frozenset[str] = frozenset({"design", "architecture", "planning", "spec"})

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
    selected_worker_model: str | None = None
    selected_worker_model_source: str | None = None
    selected_worker_backend: str | None = None


@dataclass
class DispatcherConfig:
    db_path: Path
    state_dir: Path
    max_workers: int
    poll_interval: float
    heartbeat_seconds: float
    status_heartbeat_seconds: float
    stale_recovery_seconds: float
    worker_mode: str
    default_worker_model: str | None = None
    default_codex_model: str = DEFAULT_CODEX_MODEL
    max_retries: int = 5
    notify: bool = False

    def __post_init__(self) -> None:
        # Unify: if only one is set, sync them
        if self.default_worker_model and not self.default_codex_model:
            object.__setattr__(self, "default_codex_model", self.default_worker_model)
        elif self.default_codex_model and not self.default_worker_model:
            object.__setattr__(self, "default_worker_model", self.default_codex_model)
        elif not self.default_worker_model and not self.default_codex_model:
            object.__setattr__(self, "default_worker_model", DEFAULT_CODEX_MODEL)
            object.__setattr__(self, "default_codex_model", DEFAULT_CODEX_MODEL)


@dataclass(frozen=True)
class ModelSelection:
    value: str
    source: str


# Backward-compatible alias
CodexModelSelection = ModelSelection


def snapshot_retry_count(snapshot: dict[str, Any]) -> int:
    """Return the current retry_count from a claimed task snapshot, or 0 if absent."""
    runtime = snapshot.get("runtime") or {}
    return int(runtime.get("retry_count") or 0)


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


def cleanup_legacy_runtime_dirs(paths: RuntimePaths) -> None:
    legacy_reports_dir = paths.state_dir / ".worker-reports"
    if legacy_reports_dir.exists() and legacy_reports_dir.is_dir() and not legacy_reports_dir.is_symlink():
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
            "default_codex_model": config.default_codex_model,
            "default_worker_model": config.default_worker_model or config.default_codex_model,
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
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    def __init__(self, paths: RuntimePaths):
        self.path = paths.log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def emit(self, level: str, subsystem: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {level} [{subsystem}] {message}"
        print(self._format_console_line(timestamp, level, subsystem, message))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # Pattern matches the plain-text log format written by emit():
    #   HH:MM:SS LEV [subsystem] message
    _LOG_LINE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}) (INF|WRN|ERR|DBG) \[([^\]]+)\] (.*)$")

    def colorize_log_line(self, line: str) -> str:
        """Re-parse a plain-text log line and apply color formatting for TTY output."""
        m = self._LOG_LINE_RE.match(line)
        if not m:
            return line
        timestamp, level, subsystem, message = m.group(1), m.group(2), m.group(3), m.group(4)
        return self._format_console_line(timestamp, level, subsystem, message)

    def tail(self, lines: int = 120, colorize: bool = False) -> str:
        if not self.path.exists():
            return ""
        data = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail_lines = data[-lines:]
        if colorize:
            tail_lines = [self.colorize_log_line(ln) for ln in tail_lines]
        return "\n".join(tail_lines)

    def _style(self, text: str, *codes: str) -> str:
        if not self.use_color:
            return text
        return "".join(codes) + text + self.RESET

    def _kv(self, message: str) -> dict[str, str]:
        return {key: value for key, value in re.findall(r"([A-Za-z_]+)=([^ ]+)", message)}

    def _prefix(self, timestamp: str, level: str, subsystem: str) -> str:
        level_color = {
            "INF": self.CYAN,
            "WRN": self.YELLOW,
            "ERR": self.RED,
        }.get(level, self.BLUE)
        ts = self._style(timestamp, self.DIM)
        lvl = self._style(level, self.BOLD, level_color)
        sub = self._style(f"[{subsystem}]", self.MAGENTA)
        return f"{ts} {lvl} {sub}"

    def _format_console_line(self, timestamp: str, level: str, subsystem: str, message: str) -> str:
        prefix = self._prefix(timestamp, level, subsystem)
        if subsystem != "central.dispatcher":
            return f"{prefix} {message}"

        fields = self._kv(message)
        if message.startswith("heartbeat "):
            return (
                f"{prefix} "
                f"{self._style('HEARTBEAT', self.BOLD, self.BLUE)} "
                f"state={self._style(fields.get('state', '-'), self.BOLD)} "
                f"workers={self._style(fields.get('workers', '-'), self.BOLD, self.CYAN)} "
                f"idle={self._style(fields.get('idle_slots', '-'), self.YELLOW)} "
                f"tasks={self._style(fields.get('running_tasks', '-'), self.GREEN)} "
                f"eligible={self._style(fields.get('eligible', '-'), self.CYAN)} "
                f"next={self._style(fields.get('next', '-'), self.GREEN)} "
                f"leases={self._style(fields.get('leases', '-'), self.CYAN)} "
                f"review={self._style(fields.get('review', '-'), self.YELLOW)} "
                f"failed={self._style(fields.get('failed', '-'), self.RED if fields.get('failed', '0') != '0' else self.GREEN)} "
                f"mismatch={self._style(fields.get('mismatch', '-'), self.RED if fields.get('mismatch', '0') != '0' else self.GREEN)}"
            )
        if message.startswith("worker_spawned "):
            line = (
                f"{prefix} "
                f"{self._style('START', self.BOLD, self.GREEN)} "
                f"task={self._style(fields.get('task', '-'), self.BOLD, self.GREEN)} "
                f"run={self._style(fields.get('run', '-'), self.CYAN)} "
                f"pid={self._style(fields.get('pid', '-'), self.YELLOW)} "
                f"mode={self._style(fields.get('mode', '-'), self.BLUE)}"
            )
            if fields.get("model"):
                line += f" model={self._style(fields.get('model', '-'), self.BOLD, self.CYAN)}"
            return line
        if message.startswith("worker_finished "):
            status = fields.get("runtime_status", "-")
            status_color = self.GREEN if status in {"done", "pending_review"} else self.RED
            return (
                f"{prefix} "
                f"{self._style('FINISH', self.BOLD, status_color)} "
                f"task={self._style(fields.get('task', '-'), self.BOLD)} "
                f"run={self._style(fields.get('run', '-'), self.CYAN)} "
                f"status={self._style(status, self.BOLD, status_color)}"
            )
        if message.startswith("stale_recovery "):
            return (
                f"{prefix} "
                f"{self._style('RECOVER', self.BOLD, self.YELLOW)} "
                f"recovered={self._style(fields.get('recovered', '-'), self.BOLD, self.YELLOW)}"
            )
        if message.startswith("dispatcher_started"):
            return f"{prefix} {self._style('DISPATCHER STARTED', self.BOLD, self.GREEN)} {message}"
        if message.startswith("dispatcher_stopped"):
            return f"{prefix} {self._style('DISPATCHER STOPPED', self.BOLD, self.YELLOW)}"
        if message.startswith("worker_spawn_error") or message.startswith("worker_timeout") or message.startswith("worker_heartbeat_error"):
            return f"{prefix} {self._style('ISSUE', self.BOLD, self.RED)} {message}"
        if message.startswith("dispatcher_handoff_prepared") or message.startswith("worker_adopted"):
            return f"{prefix} {self._style('HANDOFF', self.BOLD, self.YELLOW)} {message}"
        if message.startswith("worker_auto_reconciled"):
            return f"{prefix} {self._style('RECONCILE', self.BOLD, self.GREEN)} {message}"
        if message.startswith("worker_auto_reconcile_failed"):
            return f"{prefix} {self._style('RECONCILE', self.BOLD, self.RED)} {message}"
        return f"{prefix} {message}"


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


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_codex_model(value: Any, *, label: str) -> str:
    text = normalize_optional_string(value)
    if text is None:
        die(f"{label} must be a non-empty string")
    return text


def resolve_default_codex_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default codex model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_CODEX_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_CODEX_MODEL


def resolve_worker_codex_model(snapshot: dict[str, Any], dispatcher_default_codex_model: str) -> ModelSelection:
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    task_override = normalize_optional_string(execution_metadata.get("codex_model"))
    if task_override is not None:
        return ModelSelection(value=task_override, source="task_override")
    task_class = resolve_task_class(snapshot)
    policy_model, policy_source = resolve_policy_model(task_class, "codex")
    # Only apply policy if it differs from the medium-tier default; otherwise fall
    # through to dispatcher_default so operator-configured defaults still apply.
    if task_class == "design":
        return ModelSelection(value=policy_model, source=policy_source)
    return ModelSelection(
        value=normalize_codex_model(dispatcher_default_codex_model, label="dispatcher default codex model"),
        source="dispatcher_default",
    )


def resolve_default_claude_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default claude model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_CLAUDE_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_CLAUDE_MODEL


def resolve_worker_claude_model(snapshot: dict[str, Any], dispatcher_default_claude_model: str) -> ModelSelection:
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    task_override = normalize_optional_string(execution_metadata.get("claude_model"))
    if task_override is not None:
        return ModelSelection(value=task_override, source="task_override")
    task_class = resolve_task_class(snapshot)
    if task_class == "design":
        policy_model, policy_source = resolve_policy_model(task_class, "claude")
        return ModelSelection(value=policy_model, source=policy_source)
    return ModelSelection(
        value=normalize_codex_model(dispatcher_default_claude_model, label="dispatcher default claude model"),
        source="dispatcher_default",
    )


def resolve_task_class(snapshot: dict[str, Any]) -> str:
    """Return the task class ('design' or 'routine') for model policy selection.

    Detection priority:
    1. execution.metadata.task_class explicit override
    2. metadata.tags contains a high-tier tag (design, architecture, planning, spec)
    3. metadata.phase contains 'design' or 'architecture'
    4. Default: 'routine'
    """
    execution_metadata = (snapshot.get("execution") or {}).get("metadata") or {}
    explicit_class = normalize_optional_string(execution_metadata.get("task_class"))
    if explicit_class is not None:
        return explicit_class.lower()
    metadata = snapshot.get("metadata") or {}
    tags = {str(t).lower() for t in (metadata.get("tags") or [])}
    if tags & HIGH_TIER_TAGS:
        return "design"
    phase = normalize_optional_string(metadata.get("phase")) or ""
    if any(kw in phase.lower() for kw in ("design", "architecture", "planning", "spec")):
        return "design"
    return "routine"


def resolve_policy_model(task_class: str, backend: str) -> tuple[str, str]:
    """Return (model, source) for the given task_class and backend.

    Returns the high-tier model for 'design' tasks, medium-tier for everything else.
    Source tag is 'policy_default' so callers can inspect where the model came from.
    """
    if task_class == "design":
        model = HIGH_TIER_CLAUDE_MODEL if backend == "claude" else HIGH_TIER_CODEX_MODEL
    else:
        model = MEDIUM_TIER_CLAUDE_MODEL if backend == "claude" else MEDIUM_TIER_CODEX_MODEL
    return model, "policy_default"


def resolve_default_worker_model(worker_mode: str, explicit: str | None) -> str:
    """Resolve the default model for whatever backend is configured."""
    # Check generic env var first
    generic_env = normalize_optional_string(os.environ.get(DEFAULT_WORKER_MODEL_ENV))
    if explicit is not None:
        return explicit
    if generic_env is not None:
        return generic_env
    if worker_mode == "claude":
        return resolve_default_claude_model(None)
    return resolve_default_codex_model(None)


def resolve_task_worker_backend(snapshot: dict[str, Any], dispatcher_default: str) -> str:
    """Allow per-task backend override via execution.metadata.worker_backend."""
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    override = normalize_optional_string(execution_metadata.get("worker_backend"))
    if override is not None and override in ("codex", "claude", "stub"):
        return override
    return dispatcher_default


def build_worker_task(snapshot: dict[str, Any], dispatcher_default_codex_model: str, *, worker_mode: str = "codex", dispatcher_default_worker_model: str | None = None) -> dict[str, Any]:
    execution = snapshot.get("execution") or {}
    metadata = snapshot.get("metadata") or {}
    execution_metadata = execution.get("metadata") or {}
    effective_backend = resolve_task_worker_backend(snapshot, worker_mode)
    if effective_backend == "claude":
        claude_default = dispatcher_default_worker_model or resolve_default_claude_model(None)
        worker_model = resolve_worker_claude_model(snapshot, claude_default)
    else:
        codex_model = resolve_worker_codex_model(snapshot, dispatcher_default_codex_model)
    deliverables = extract_markdown_items(snapshot.get("deliverables_md", "")) or [snapshot.get("deliverables_md", "").strip()]
    scope_notes = extract_markdown_items(snapshot.get("scope_md", "")) or [snapshot.get("scope_md", "").strip()]
    validation_commands = extract_markdown_items(snapshot.get("testing_md", "")) or [snapshot.get("testing_md", "").strip()]
    validation_commands = [item for item in validation_commands if item]
    deliverables = [item for item in deliverables if item]
    scope_notes = [item for item in scope_notes if item]
    rework_context = str(metadata.get("rework_context") or "").strip()
    rework_count = int(metadata.get("rework_count") or 0)
    prompt_sections = []
    if rework_context:
        prompt_sections.append(
            f"## REWORK (attempt {rework_count})\n"
            f"A previous attempt failed audit. Fix **only** the specific issues listed below.\n"
            f"Do not explore unrelated code or documents. Make targeted changes only.\n\n"
            f"{rework_context}"
        )
    prompt_sections += [
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
    result = {
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
        "worker_backend": effective_backend,
        "sandbox_mode": execution.get("sandbox_mode"),
        "approval_policy": execution.get("approval_policy"),
        "additional_writable_dirs_json": json.dumps(execution.get("additional_writable_dirs") or []),
    }
    # Add backend-specific model fields
    if effective_backend == "claude":
        result["worker_model"] = worker_model.value
        result["worker_model_source"] = worker_model.source
    elif effective_backend == "codex":
        result["codex_profile"] = execution_metadata.get("codex_profile") or AUTONOMY_PROFILE
        result["codex_model"] = codex_model.value
        result["codex_model_source"] = codex_model.source
        # Generic aliases for codex
        result["worker_model"] = codex_model.value
        result["worker_model_source"] = codex_model.source
    else:
        result["worker_model"] = None
        result["worker_model_source"] = None
    return result


def load_autonomy_runner():
    if str(AUTONOMY_ROOT) not in sys.path:
        sys.path.insert(0, str(AUTONOMY_ROOT))
    from autonomy import runner as autonomy_runner  # type: ignore

    return autonomy_runner


def build_claude_command(worker_task: dict[str, Any], result_path: Path, model: str) -> list[str]:
    """Build a shell command that runs claude -p and converts output to worker_result schema."""
    task_id = worker_task.get("id") or worker_task.get("task_id") or "unknown"
    run_id = worker_task.get("run_id") or "unknown"
    # Shell script: run claude, capture raw JSON, convert to worker_result schema, write to result_path
    script = (
        "import json, subprocess, sys\n"
        "proc = subprocess.run(\n"
        f"    ['claude', '-p', '--dangerously-skip-permissions', '--model', {model!r}, '--output-format', 'json'],\n"
        "    stdin=sys.stdin, capture_output=True, text=True\n"
        ")\n"
        "raw = proc.stdout.strip()\n"
        "try:\n"
        "    claude_result = json.loads(raw) if raw else {}\n"
        "except json.JSONDecodeError:\n"
        "    claude_result = {'result': raw}\n"
        "is_error = claude_result.get('is_error', False) or claude_result.get('type') == 'error'\n"
        "summary = str(claude_result.get('result', '') or claude_result.get('error', {}).get('message', 'no result'))[:2000]\n"
        "payload = {\n"
        "    'schema_version': 1,\n"
        f"    'task_id': {task_id!r},\n"
        f"    'run_id': {run_id!r},\n"
        "    'status': 'FAILED' if is_error or proc.returncode != 0 else 'COMPLETED',\n"
        "    'summary': summary,\n"
        "    'completed_items': [summary] if not is_error else [],\n"
        "    'remaining_items': [],\n"
        "    'decisions': [],\n"
        "    'discoveries': [],\n"
        "    'blockers': [],\n"
        "    'validation': [],\n"
        "    'artifacts': [],\n"
        "    'claude_raw': claude_result,\n"
        "}\n"
        "from pathlib import Path\n"
        f"Path({str(result_path)!r}).write_text(json.dumps(payload, indent=2), encoding='utf-8')\n"
        "print(json.dumps({'status': payload['status'], 'summary_preview': summary[:200]}))\n"
        "sys.exit(proc.returncode)\n"
    )
    return [sys.executable, "-c", script]


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


class WorkerBackend(abc.ABC):
    """Protocol for backend-specific worker spawn preparation."""

    @abc.abstractmethod
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        """Return (prompt_text, command, stdin_mode) for subprocess.Popen."""


class CodexBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        worker_task["run_id"] = run_id
        autonomy_runner = load_autonomy_runner()
        prompt_text = autonomy_runner.build_prompt(
            worker_task,
            autonomy_runner.normalize_dependency_context(
                [
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
            ),
        )
        command = autonomy_runner.build_codex_command(worker_task, result_path, AUTONOMY_SCHEMA_PATH)
        return prompt_text, command, subprocess.PIPE


class ClaudeBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        command = build_claude_command(worker_task, result_path, worker_task["worker_model"])
        return prompt_text, command, subprocess.PIPE


def normalize_claude_result(log_path: Path, result_path: Path, task_id: str, run_id: str) -> bool:
    """Parse claude -p JSON output from log_path and write normalized worker_result to result_path.

    claude -p --output-format json writes a final JSON line to stdout of the form:
        {"type": "result", "subtype": "success", "is_error": false, "result": "...", ...}

    The reaper expects result_path to contain worker_result.schema.json format:
        {"status": "COMPLETED", "schema_version": 1, "task_id": "...", "summary": "...", ...}

    Returns True if a result line was found and written, False otherwise.
    """
    if not log_path.exists():
        return False
    claude_result: dict[str, Any] | None = None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "result":
                    claude_result = obj
                    break
            except json.JSONDecodeError:
                continue
    except Exception:
        return False
    if claude_result is None:
        return False
    is_error = bool(claude_result.get("is_error"))
    raw_result = str(claude_result.get("result") or "")
    status = "FAILED" if is_error else "COMPLETED"
    payload: dict[str, Any] = {
        "status": status,
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "summary": raw_result[:2000] if raw_result else ("claude worker error" if is_error else "claude worker completed"),
        "completed_items": [] if is_error else ["claude worker run finished"],
        "remaining_items": [],
        "decisions": [],
        "discoveries": [],
        "blockers": [],
        "validation": [{"name": "claude-exit", "passed": not is_error, "notes": f"is_error={is_error}"}],
        "files_changed": [],
        "warnings": [],
        "artifacts": [],
        "_claude_meta": {
            "subtype": claude_result.get("subtype"),
            "session_id": claude_result.get("session_id"),
            "num_turns": claude_result.get("num_turns"),
            "cost_usd": claude_result.get("cost_usd"),
            "duration_ms": claude_result.get("duration_ms"),
        },
    }
    try:
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception:
        return False


class StubBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        command = build_stub_command(snapshot, run_id, result_path)
        return prompt_text, command, subprocess.DEVNULL


_WORKER_BACKENDS: dict[str, WorkerBackend] = {
    "codex": CodexBackend(),
    "claude": ClaudeBackend(),
    "stub": StubBackend(),
}


def get_worker_backend(name: str) -> WorkerBackend:
    """Return the WorkerBackend for the given backend name, defaulting to StubBackend."""
    return _WORKER_BACKENDS.get(name, _WORKER_BACKENDS["stub"])


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
        }
    return {
        "prompt": select_latest_artifact_path(artifacts, ".md"),
        "log": select_latest_artifact_path(artifacts, ".log"),
        "result": select_latest_artifact_path(artifacts, ".json"),
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
            log_delta = (f"+{_fmt_bytes(growth_bytes)}" if isinstance(growth_bytes, int) and growth_bytes > 0
                         else (f"{_fmt_bytes(growth_bytes)}" if isinstance(growth_bytes, int) else "-"))
            log_signal = (log.get("signal") or {}).get("state") or "-"
            lines.append(
                (
                    f"- {worker['observed_state']}: {worker['task_id']} run={worker.get('run_id') or '-'} "
                    f"runtime={worker['runtime_status']} heartbeat_age={_fmt_seconds((worker.get('heartbeat') or {}).get('age_seconds'))}s "
                    f"log_size={log_size} log_delta={log_delta} log_signal={log_signal} "
                    f"model={((worker.get('worker') or {}).get('model') or '-')} reason={worker.get('reason', '-')}"
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
                    f"model={((worker.get('worker') or {}).get('model') or '-')} reason={worker['reason']}"
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
        self._capacity_backoff_until: float = 0.0

    def _capacity_backoff_active(self) -> bool:
        """Return True if the dispatcher is currently in a capacity backoff window."""
        return time.monotonic() < self._capacity_backoff_until

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
        payload = {
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
        if state.selected_worker_model is not None:
            payload["worker_model"] = state.selected_worker_model
            # Backward compat: also write codex_model for codex backend
            if state.selected_worker_backend == "codex":
                payload["codex_model"] = state.selected_worker_model
        if state.selected_worker_model_source is not None:
            payload["worker_model_source"] = state.selected_worker_model_source
            if state.selected_worker_backend == "codex":
                payload["codex_model_source"] = state.selected_worker_model_source
        if state.selected_worker_backend is not None:
            payload["worker_backend"] = state.selected_worker_backend
        return payload

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
            # actionable_failed: only tasks still open from the planner's perspective
            actionable_failed = int(conn.execute(
                "SELECT COUNT(*) AS c FROM task_runtime_state trs"
                " JOIN tasks t ON t.task_id = trs.task_id"
                " WHERE trs.runtime_status = 'failed'"
                " AND t.planner_status NOT IN ('done', 'cancelled')"
            ).fetchone()["c"])
            mismatch_count = sum(1 for snapshot in snapshots if snapshot.get("status_mismatch"))
        finally:
            conn.close()
        return {
            "eligible_count": len(eligible),
            "next_eligible_task_id": eligible[0]["task_id"] if eligible else None,
            "runtime_counts": counts,
            "actionable_failed": actionable_failed,
            "active_leases": active_leases,
            "mismatch_count": mismatch_count,
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
        idle_slots = max(0, self.config.max_workers - len(active_ids))
        backoff_remaining = max(0.0, self._capacity_backoff_until - time.monotonic())
        backoff_str = f" quota_backoff={backoff_remaining:.0f}s" if backoff_remaining > 0 else ""
        self.logger.emit(
            "INF",
            "central.dispatcher",
            (
                "heartbeat "
                f"state={'stopping' if self._stop_requested else 'running'} "
                f"workers={len(active_ids)}/{self.config.max_workers} "
                f"idle_slots={idle_slots} "
                f"running_tasks={self._format_task_ids(active_ids)} "
                f"eligible={snapshot['eligible_count']} "
                f"next={snapshot['next_eligible_task_id'] or '-'} "
                f"leases={snapshot['active_leases']} "
                f"parked={snapshot['runtime_counts'].get('parked', 0)} "
                f"review={snapshot['runtime_counts'].get('pending_review', 0)} "
                f"failed={snapshot['actionable_failed']} "
                f"mismatch={snapshot['mismatch_count']}"
                f"{backoff_str}"
            ),
        )
        # Notify if dispatcher is fully stalled: backoff active, no running workers, tasks waiting
        if (
            backoff_remaining > 0
            and len(active_ids) == 0
            and snapshot["eligible_count"] > 0
            and not getattr(self, "_quota_stall_notified", False)
        ):
            self._quota_stall_notified = True
            self._maybe_notify(
                title="⚠️ Dispatcher stalled — quota limit",
                runtime_status="failed",
                summary=f"{snapshot['eligible_count']} tasks waiting, no workers running. Switch backend or wait {backoff_remaining:.0f}s.",
            )
        elif backoff_remaining == 0:
            self._quota_stall_notified = False
        self._last_status_heartbeat_monotonic = now

    def _claim_next(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return task_db.runtime_claim(
                conn,
                worker_id=derive_worker_id("slot"),
                queue_name="default",
                lease_seconds=max(5, int(self.config.heartbeat_seconds * 3)),
                task_id=None,
                actor_id="central.dispatcher",
                raise_on_empty=False,
            )
        finally:
            conn.close()

    def _spawn_worker(self, snapshot: dict[str, Any]) -> None:
        effective_backend = resolve_task_worker_backend(snapshot, self.config.worker_mode)
        worker_task = build_worker_task(
            snapshot,
            self.config.default_codex_model,
            worker_mode=effective_backend,
            dispatcher_default_worker_model=self.config.default_worker_model,
        )
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

        backend = get_worker_backend(effective_backend)
        prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, run_id, result_path)

        prompt_path.write_text(prompt_text, encoding="utf-8")
        log_handle = log_path.open("a", encoding="utf-8")

        conn = self._connect()
        try:
            with conn:
                runtime_notes = [f"worker_mode={effective_backend}"]
                if worker_task.get("worker_model"):
                    runtime_notes.append(f"model={worker_task['worker_model']}")
                    runtime_notes.append(f"model_source={worker_task.get('worker_model_source', '-')}")
                task_db.runtime_transition(
                    conn,
                    task_id=snapshot["task_id"],
                    status="running",
                    worker_id=(snapshot.get("lease") or {}).get("lease_owner_id"),
                    error_text=None,
                    notes=" ".join(runtime_notes),
                    artifacts=[],
                    actor_id="central.dispatcher",
                    effective_worker_model=worker_task.get("worker_model") or None,
                    worker_model_source=worker_task.get("worker_model_source") or None,
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
            selected_worker_model=str(worker_task["worker_model"]) if worker_task.get("worker_model") else None,
            selected_worker_model_source=str(worker_task["worker_model_source"]) if worker_task.get("worker_model_source") else None,
            selected_worker_backend=effective_backend,
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
            " ".join(
                item
                for item in [
                    f"worker_spawned task={snapshot['task_id']} run={run_id} pid={proc.pid} mode={effective_backend}",
                    f"model={worker_task.get('worker_model', '-')}",
                    f"model_source={worker_task.get('worker_model_source', '-')}",
                ]
                if item
            ),
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
                selected_worker_model=normalize_optional_string(supervision.get("worker_model") or supervision.get("codex_model")),
                selected_worker_model_source=normalize_optional_string(supervision.get("worker_model_source") or supervision.get("codex_model_source")),
                selected_worker_backend=normalize_optional_string(supervision.get("worker_backend") or supervision.get("worker_mode")),
            )
            if not process_matches(state.pid, state.process_start_token):
                if state.result_path.exists() or state.log_path.exists() or state.prompt_path.exists():
                    handoff_state = (metadata.get("handoff") or {}).get("state", "")
                    was_interrupted = handoff_state in {"interrupted_by_restart", "pending_adoption"}
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_reconcile_post_restart task={snapshot['task_id']} run={state.run_id} pid={state.pid} interrupted={was_interrupted}",
                    )
                    self._finalize_worker(state, timed_out=False, interrupted_by_restart=was_interrupted)
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

    _CODEX_USAGE_LIMIT_MARKER = "you've hit your usage limit"
    _CLAUDE_QUOTA_MARKERS = (
        "rate limit exceeded",
        "rate_limit_error",
        "too many requests",
        "overloaded_error",
        "529",  # Anthropic overload status
        "quota exceeded",
        "usage limit",
    )

    @staticmethod
    def _detect_codex_capacity_hit(log_path: Any) -> bool:
        """Return True if the codex log file contains a usage-limit signal."""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace").lower()
            return CentralDispatcher._CODEX_USAGE_LIMIT_MARKER in text
        except Exception:
            return False

    @staticmethod
    def _detect_claude_capacity_hit(log_path: Any) -> bool:
        """Return True if the claude log file contains a rate-limit or quota signal."""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace").lower()
            return any(m in text for m in CentralDispatcher._CLAUDE_QUOTA_MARKERS)
        except Exception:
            return False

    def _maybe_notify(self, *, title: str, runtime_status: str, summary: str | None = None) -> None:
        if not self.config.notify:
            return
        import subprocess
        if runtime_status in {"done", "pending_review"}:
            notif_title = f"\u2705 {title}"
            sound = "Glass"
        elif runtime_status == "timeout":
            notif_title = f"\u23f1 {title}"
            sound = "Basso"
        else:
            notif_title = f"\u274c {title}"
            sound = "Basso"
        body = (summary or runtime_status)[:100].replace('"', '\\"')
        notif_title = notif_title.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{notif_title}" sound name "{sound}"'],
            check=False,
        )

    def _finalize_worker(self, state: ActiveWorker, *, timed_out: bool = False, interrupted_by_restart: bool = False) -> None:
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
                self._maybe_notify(title=str(state.task.get("title") or task_id), runtime_status="timeout", summary="hard timeout exceeded")
                return

            runtime_status = "failed"
            error_text = None
            notes = None
            tests = None
            result = None
            extra_artifacts: list[tuple[str, str, dict[str, Any]]] = []
            result_artifacts = terminal_artifacts.copy()
            # For Claude workers, claude -p JSON output goes to the log file (stdout=log_handle).
            # result_path is never written by the subprocess itself, so normalize it here before
            # the result_path.exists() check below.
            if not state.result_path.exists() and getattr(state, "selected_worker_backend", None) == "claude":
                if normalize_claude_result(state.log_path, state.result_path, task_id, state.run_id):
                    self.logger.emit("INF", "central.dispatcher", f"claude_result_normalized task={task_id} run={state.run_id}")
                else:
                    self.logger.emit("INF", "central.dispatcher", f"claude_result_normalize_failed task={task_id} run={state.run_id}")
            if state.result_path.exists():
                result_artifacts.append(str(state.result_path))
                try:
                    autonomy_runner = load_autonomy_runner()
                    result = autonomy_runner.load_result_file(state.result_path, task_id=task_id, run_id=state.run_id)
                    runtime_status = success_runtime_status(state.task) if result.status == "COMPLETED" else "failed"
                    notes = result.summary
                    tests = summarize_validation_results(result.validation)
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
                if interrupted_by_restart:
                    error_text = "interrupted_by_restart"
                    notes = "worker was interrupted by dispatcher restart or force-stop before result emission"
                elif state.proc is not None:
                    rc = state.proc.returncode
                    if rc is not None and rc == 0:
                        error_text = "result_emit_failed"
                        notes = "worker exited cleanly (exit 0) but did not write result file"
                    else:
                        code_str = str(rc) if rc is not None else "unknown"
                        error_text = f"worker_crashed (exit {code_str})"
                        notes = f"worker process exited with code {code_str}"
                else:
                    error_text = "worker_crashed"
                    notes = "worker process not found; presumed crashed before result emission"

            # Detect capacity/quota limits: requeue instead of failing, notify operator
            backend = str(getattr(state, "selected_worker_backend", None) or "")
            _quota_hit = (
                runtime_status == "failed"
                and (
                    (backend == "codex" and self._detect_codex_capacity_hit(state.log_path))
                    or (backend == "claude" and self._detect_claude_capacity_hit(state.log_path))
                )
            )
            if _quota_hit:
                requeue_conn = self._connect()
                try:
                    task_db.runtime_requeue_task(
                        requeue_conn,
                        task_id=task_id,
                        actor_id="central.dispatcher",
                        reason=f"{backend} usage limit reached; requeueing for later dispatch",
                        reset_retry_count=True,
                    )
                finally:
                    requeue_conn.close()
                backoff_seconds = 300
                self._capacity_backoff_until = time.monotonic() + backoff_seconds
                task_title = str(state.task.get("title") or task_id)
                self.logger.emit(
                    "WRN",
                    "central.dispatcher",
                    f"worker_quota_hit task={task_id} run={state.run_id} backend={backend} backoff_seconds={backoff_seconds}",
                )
                self._maybe_notify(
                    title=f"Quota limit hit — {backend}",
                    runtime_status="failed",
                    summary=f"{task_title} requeued; dispatcher backing off {backoff_seconds}s. Switch worker backend if persistent.",
                )
            else:
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
                self._maybe_notify(title=str(state.task.get("title") or task_id), runtime_status=runtime_status, summary=notes or error_text)
                if runtime_status == "done":
                    reconcile_conn = self._connect()
                    try:
                        is_audit_task = str(state.task.get("task_type") or "") == "audit"
                        verdict = str(getattr(result, "verdict", "") or "") if result is not None else ""
                        # Audit task with rework_required verdict: auto-requeue parent with findings
                        if is_audit_task and verdict == "rework_required":
                            rework_snap = task_db.reconcile_audit_rework(
                                reconcile_conn,
                                audit_task_id=task_id,
                                summary=notes or "audit verdict: rework_required",
                                actor_id="central.dispatcher",
                            )
                            audit_meta = rework_snap.get("metadata") or {}
                            parent_id = audit_meta.get("parent_task_id") or "?"
                            try:
                                parent_snap = task_db.fetch_task_snapshots(reconcile_conn, task_id=parent_id)
                                rework_count = (parent_snap[0].get("metadata") or {}).get("rework_count", "?") if parent_snap else "?"
                                parent_status = parent_snap[0].get("planner_status", "?") if parent_snap else "?"
                            except Exception:
                                rework_count, parent_status = "?", "?"
                            self.logger.emit(
                                "INF",
                                "central.dispatcher",
                                f"worker_audit_rework task={task_id} run={state.run_id} parent={parent_id} rework_count={rework_count} parent_status={parent_status}",
                            )
                        # Audit task with accepted verdict: close audit + auto-close parent
                        elif is_audit_task and verdict in {"accepted", "pass", "passed", "done", ""}:
                            pass_snap = task_db.reconcile_audit_pass(
                                reconcile_conn,
                                audit_task_id=task_id,
                                summary=notes or "audit verdict: accepted",
                                actor_id="central.dispatcher",
                            )
                            audit_meta = pass_snap.get("metadata") or {}
                            parent_id = audit_meta.get("parent_task_id") or "?"
                            try:
                                parent_snap = task_db.fetch_task_snapshots(reconcile_conn, task_id=parent_id)
                                parent_status = parent_snap[0].get("planner_status", "?") if parent_snap else "?"
                            except Exception:
                                parent_status = "?"
                            self.logger.emit(
                                "INF",
                                "central.dispatcher",
                                f"worker_audit_pass task={task_id} run={state.run_id} parent={parent_id} parent_status={parent_status}",
                            )
                        else:
                            reconciled = task_db.auto_reconcile_runtime_success(
                                reconcile_conn,
                                task_id=task_id,
                                summary=notes or "runtime completed successfully",
                                notes=notes,
                                tests=tests,
                                artifacts=result_artifacts,
                                actor_id="central.dispatcher",
                                run_id=state.run_id,
                            )
                            reconciled_planner_status = reconciled["planner_status"]
                            if reconciled_planner_status == "awaiting_audit":
                                self.logger.emit(
                                    "INF",
                                    "central.dispatcher",
                                    f"worker_auto_reconcile_skipped task={task_id} run={state.run_id} reason=awaiting_audit",
                                )
                            else:
                                self.logger.emit(
                                    "INF",
                                    "central.dispatcher",
                                    f"worker_auto_reconciled task={task_id} run={state.run_id} planner_status={reconciled_planner_status} version={reconciled['version']}",
                                )
                    except Exception as exc:
                        with reconcile_conn:
                            task_db.insert_event(
                                reconcile_conn,
                                task_id=task_id,
                                event_type="planner.task_auto_reconcile_failed",
                                actor_kind="runtime",
                                actor_id="central.dispatcher",
                                payload={"run_id": state.run_id, "error": str(exc)},
                            )
                        self.logger.emit(
                            "ERR",
                            "central.dispatcher",
                            f"worker_auto_reconcile_failed task={task_id} run={state.run_id} error={exc}",
                        )
                    finally:
                        reconcile_conn.close()
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

    def _abort_if_max_retries(self, snapshot: dict[str, Any]) -> bool:
        """Transition task to failed with max_retries_exceeded if retry_count >= max_retries.

        Returns True if the task was aborted, False if it should proceed to spawn.
        """
        retry_count = snapshot_retry_count(snapshot)
        if retry_count < self.config.max_retries:
            return False
        task_id = snapshot["task_id"]
        worker_id = (snapshot.get("lease") or {}).get("lease_owner_id")
        self.logger.emit(
            "WRN",
            "central.dispatcher",
            f"max_retries_exceeded task={task_id} retry_count={retry_count} max_retries={self.config.max_retries}",
        )
        conn = self._connect()
        try:
            with conn:
                task_db.runtime_transition(
                    conn,
                    task_id=task_id,
                    status="failed",
                    worker_id=worker_id,
                    error_text="max_retries_exceeded",
                    notes=f"retry_count={retry_count} reached max_retries={self.config.max_retries}; halting automatic retry",
                    artifacts=[],
                    actor_id="central.dispatcher",
                )
        finally:
            conn.close()
        return True

    def _fill_workers(self) -> None:
        while len(self._active) < self.config.max_workers and not self._stop_requested:
            snapshot = self._claim_next()
            if snapshot is None:
                break
            if self._abort_if_max_retries(snapshot):
                continue
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
        if self._abort_if_max_retries(snapshot):
            if emit_result:
                print(json_dumps({"claimed": True, "task_id": snapshot["task_id"], "aborted": "max_retries_exceeded"}))
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
                f"worker_mode={self.config.worker_mode} "
                f"default_worker_model={self.config.default_worker_model or self.config.default_codex_model} "
                f"adopted_workers={adopted}"
            ),
        )
        self._emit_status_heartbeat(force=True)
        try:
            while True:
                self._process_active()
                if self._force_stop:
                    for state in self._active.values():
                        try:
                            self._persist_worker_supervision(
                                state,
                                handoff={
                                    "state": "interrupted_by_restart",
                                    "interrupted_at": utc_now(),
                                    "interrupted_by_dispatcher_pid": os.getpid(),
                                },
                                event_type="runtime.worker_interrupted",
                                event_payload={
                                    "run_id": state.run_id,
                                    "worker_id": state.worker_id,
                                    "reason": "force_stop",
                                },
                            )
                        except Exception:
                            pass
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
    configured_default_codex_model = None
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
        configured_default_codex_model = normalize_optional_string(payload.get("default_codex_model"))
        configured_default_worker_model = normalize_optional_string(payload.get("default_worker_model")) or configured_default_codex_model
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
        # Python-based follow so we can colorize each line as it arrives.
        log_path = paths.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Emit the last N lines first, then stream new ones.
        initial = log.tail(lines=args.lines, colorize=is_tty)
        if initial:
            print(initial, flush=True)
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)  # seek to end
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
    return DispatcherConfig(
        db_path=task_db.resolve_db_path(args.db_path),
        state_dir=resolve_state_dir(args.state_dir),
        max_workers=args.max_workers,
        poll_interval=args.poll_interval,
        heartbeat_seconds=args.heartbeat_seconds,
        status_heartbeat_seconds=args.status_heartbeat_seconds,
        stale_recovery_seconds=args.stale_recovery_seconds,
        worker_mode=args.worker_mode,
        default_worker_model=resolve_default_worker_model(
            args.worker_mode,
            getattr(args, "default_worker_model", None) or getattr(args, "default_codex_model", None),
        ),
        notify=getattr(args, "notify", False),
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
            default_codex_model=DEFAULT_CODEX_MODEL,
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
                ("CENTRAL-RUNTIME-SMOKE",),
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
        sub.add_argument("--worker-mode", choices=["codex", "claude", "stub"], default=os.environ.get("CENTRAL_WORKER_MODE", "codex"))
        sub.add_argument("--default-codex-model")
        sub.add_argument("--default-worker-model", "--worker-model")
        sub.add_argument("--notify", action="store_true", default=False)
        sub.set_defaults(func=func)

    self_check_parser = subparsers.add_parser("self-check", help="Run a stub-mode CENTRAL runtime smoke check")
    self_check_parser.set_defaults(func=command_self_check)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
