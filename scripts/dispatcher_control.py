#!/usr/bin/env python3
"""Operator wrapper for the CENTRAL-native dispatcher."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import central_runtime_v2 as central_runtime
from central_runtime_v2.config import ALLOWED_CODEX_MODELS, ALLOWED_GEMINI_MODELS, ALLOWED_GROK_MODELS, ALLOWED_REASONING_EFFORTS

ALLOWED_CLAUDE_MODELS: list[str] = [
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]

ALLOWED_GROK_MODELS_LIST: list[str] = sorted(ALLOWED_GROK_MODELS)

REPO_DIR = Path(os.environ.get("CENTRAL_DISPATCHER_REPO_DIR", str(Path(__file__).resolve().parents[1]))).expanduser().resolve()
RUNTIME_SCRIPT = Path(os.environ.get("CENTRAL_DISPATCHER_RUNTIME_SCRIPT", str(REPO_DIR / "scripts" / "central_runtime_v2" / "__main__.py")))
DB_SCRIPT = Path(os.environ.get("CENTRAL_DISPATCHER_DB_SCRIPT", str(REPO_DIR / "scripts" / "central_task_db.py")))
PYTHON_BIN = sys.executable or "/usr/bin/python3"
DB_PATH = os.environ.get("CENTRAL_TASK_DB_PATH")
STATE_DIR = Path(os.environ.get("CENTRAL_RUNTIME_STATE_DIR", str(REPO_DIR / "state" / "central_runtime"))).expanduser()
LOCK_PATH = STATE_DIR / "dispatcher.lock"
LOG_PATH = STATE_DIR / "dispatcher.log"
LAUNCH_LOG_PATH = STATE_DIR / "dispatcher-launcher.log"
CONFIG_PATH = STATE_DIR / "dispatcher-config.json"
DEFAULT_MAX_WORKERS = 1
MAX_WORKERS_ENV = "CENTRAL_DISPATCHER_MAX_WORKERS"
CODEX_MODEL_ENV = central_runtime.DEFAULT_CODEX_MODEL_ENV
DEFAULT_CODEX_MODEL = central_runtime.DEFAULT_CODEX_MODEL
WORKER_MODEL_ENV = central_runtime.DEFAULT_WORKER_MODEL_ENV
CODEX_EFFORT_ENV = "CENTRAL_DISPATCHER_CODEX_EFFORT"
LOG_LEVEL_RE = re.compile(r"^(?P<ts>\S+)\s+(?P<level>[A-Z]+)\s+\[(?P<component>[^\]]+)\]\s+(?P<message>.*)$")


@dataclass(frozen=True)
class ResolvedMaxWorkers:
    value: int
    source: str


@dataclass(frozen=True)
class ResolvedWorkerModel:
    value: str
    source: str


# Backward-compatible alias
ResolvedCodexModel = ResolvedWorkerModel


class MenuExitRequested(Exception):
    """Raised when operator aborts interactive menu input."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def validate_model_for_mode(model: str, mode: str) -> None:
    if mode == "codex" and model not in ALLOWED_CODEX_MODELS:
        die(
            f"invalid codex model: {model!r}\n"
            f"Allowed: {', '.join(sorted(ALLOWED_CODEX_MODELS))}"
        )
    if mode == "gemini" and model not in ALLOWED_GEMINI_MODELS:
        die(
            f"invalid gemini model: {model!r}\n"
            f"Allowed: {', '.join(sorted(ALLOWED_GEMINI_MODELS))}"
        )
    if mode == "grok" and model not in ALLOWED_GROK_MODELS:
        die(
            f"invalid grok model: {model!r}\n"
            f"Allowed: {', '.join(sorted(ALLOWED_GROK_MODELS))}"
        )


def validate_runtime_importable() -> None:
    if not RUNTIME_SCRIPT.exists():
        die(f"CENTRAL runtime script missing: {RUNTIME_SCRIPT}")
    result = subprocess.run(
        [PYTHON_BIN, str(RUNTIME_SCRIPT), "--help"],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "CENTRAL runtime script import check failed").strip()
        die(f"CENTRAL runtime script import check failed: {RUNTIME_SCRIPT}\n{message}")


def ensure_runtime() -> None:
    if not RUNTIME_SCRIPT.exists():
        die(f"CENTRAL runtime script missing: {RUNTIME_SCRIPT}")
    if not DB_SCRIPT.exists():
        die(f"CENTRAL DB script missing: {DB_SCRIPT}")


def parse_positive_int(raw: str, *, label: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        die(f"{label} must be an integer: {raw!r}")
        raise exc
    if value < 1:
        die(f"{label} must be >= 1: {value}")
    return value


def argparse_positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {raw!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return value


def resolve_effort() -> tuple[str, str]:
    raw = os.environ.get(CODEX_EFFORT_ENV, central_runtime.DEFAULT_CODEX_EFFORT).strip().lower()
    source = "env" if CODEX_EFFORT_ENV in os.environ else "default"
    if not raw:
        raw = central_runtime.DEFAULT_CODEX_EFFORT
        source = "default"
    if raw not in ALLOWED_REASONING_EFFORTS:
        die(
            f"invalid codex effort: {raw!r}\n"
            f"Allowed: {', '.join(sorted(ALLOWED_REASONING_EFFORTS))}"
        )
    return raw, source


def runtime_cmd(*args: str) -> list[str]:
    command = [PYTHON_BIN, str(RUNTIME_SCRIPT), *args]
    if DB_PATH:
        command.extend(["--db-path", DB_PATH])
    command.extend(["--state-dir", str(STATE_DIR)])
    return command


def db_cmd(*args: str) -> list[str]:
    command = [PYTHON_BIN, str(DB_SCRIPT), *args]
    if DB_PATH:
        command.extend(["--db-path", DB_PATH])
    return command


def db_init_cmd() -> list[str]:
    return db_cmd("init", "--json")


def init_db() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(db_init_cmd(), cwd=str(REPO_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        die((result.stderr or result.stdout or "CENTRAL DB init failed").strip())


def read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def read_lock_payload() -> dict[str, object] | None:
    return read_json_file(LOCK_PATH)


def running_pid() -> int | None:
    payload = read_lock_payload()
    if payload is None:
        return None
    try:
        pid = int(payload.get("pid"))
    except Exception:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def running_lock_payload() -> dict[str, object] | None:
    return read_lock_payload() if running_pid() else None


def pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_start_token(pid: int | None) -> str | None:
    if pid is None or pid <= 0:
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


def terminate_worker(kill_target: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(kill_target, dict):
        return {"attempted": False, "terminated": False, "worker_present": False}
    raw_pid = kill_target.get("worker_pid")
    try:
        pid = int(raw_pid) if raw_pid is not None else None
    except (TypeError, ValueError):
        pid = None
    expected_token = str(kill_target.get("worker_process_start_token") or "") or None
    if pid is None:
        return {"attempted": False, "terminated": False, "worker_present": False}
    if not process_matches(pid, expected_token):
        return {"attempted": False, "terminated": False, "worker_present": False, "pid": pid}
    try:
        os.kill(pid, 15)
    except OSError:
        return {"attempted": True, "terminated": False, "worker_present": False, "pid": pid}
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not process_matches(pid, expected_token):
            return {"attempted": True, "terminated": True, "worker_present": True, "pid": pid}
        time.sleep(0.1)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not process_matches(pid, expected_token):
            return {"attempted": True, "terminated": True, "worker_present": True, "pid": pid, "forced": True}
        time.sleep(0.1)
    return {"attempted": True, "terminated": False, "worker_present": True, "pid": pid, "forced": True}


def load_saved_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"invalid dispatcher config {CONFIG_PATH}: {exc}")
    if not isinstance(payload, dict):
        die(f"invalid dispatcher config {CONFIG_PATH}: expected JSON object")
    max_workers = payload.get("max_workers")
    if max_workers is not None:
        payload["max_workers"] = parse_positive_int(str(max_workers), label=f"{CONFIG_PATH} max_workers")
    default_worker_model = payload.get("default_worker_model")
    if default_worker_model is not None:
        payload["default_worker_model"] = central_runtime.normalize_codex_model(
            default_worker_model,
            label=f"{CONFIG_PATH} default_worker_model",
        )
    else:
        default_codex_model = payload.get("default_codex_model")
        if default_codex_model is not None:
            payload["default_worker_model"] = central_runtime.normalize_codex_model(
                default_codex_model,
                label=f"{CONFIG_PATH} default_codex_model",
            )
    return payload


def saved_max_workers() -> int | None:
    payload = load_saved_config()
    value = payload.get("max_workers")
    return int(value) if value is not None else None


def saved_codex_model() -> str | None:
    payload = load_saved_config()
    value = payload.get("default_worker_model") or payload.get("default_codex_model")
    return str(value) if value is not None else None


def saved_worker_model() -> str | None:
    payload = load_saved_config()
    value = payload.get("default_worker_model")
    return str(value) if value is not None else None


def saved_worker_mode() -> str | None:
    payload = load_saved_config()
    value = payload.get("worker_mode")
    return str(value) if value is not None else None


def env_max_workers() -> int | None:
    raw = os.environ.get(MAX_WORKERS_ENV)
    if raw is None or not raw.strip():
        return None
    return parse_positive_int(raw, label=MAX_WORKERS_ENV)


def env_codex_model() -> str | None:
    raw = os.environ.get(CODEX_MODEL_ENV)
    if raw is None or not raw.strip():
        return None
    return central_runtime.normalize_codex_model(raw, label=CODEX_MODEL_ENV)


def env_worker_model() -> str | None:
    raw = os.environ.get(WORKER_MODEL_ENV)
    if raw is None or not raw.strip():
        return None
    return central_runtime.normalize_codex_model(raw, label=WORKER_MODEL_ENV)


def saved_notify() -> bool | None:
    payload = load_saved_config()
    value = payload.get("notify")
    return bool(value) if value is not None else None


def saved_audit_model() -> str | None:
    payload = load_saved_config()
    return payload.get("audit_worker_model")


def save_config(*, max_workers: int | None = None, codex_model: str | None = None, worker_model: str | None = None, worker_mode: str | None = None, notify: bool | None = None, audit_model: str | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = load_saved_config()
    if max_workers is not None:
        payload["max_workers"] = max_workers
    if worker_model is not None:
        payload["default_worker_model"] = central_runtime.normalize_codex_model(worker_model, label="worker model")
    elif codex_model is not None:
        payload["default_worker_model"] = central_runtime.normalize_codex_model(codex_model, label="codex model")
    if worker_mode is not None:
        if worker_mode not in ("codex", "claude", "gemini", "grok", "stub"):
            die(f"invalid worker mode: {worker_mode}")
        payload["worker_mode"] = worker_mode
    if notify is not None:
        payload["notify"] = notify
    if audit_model is not None:
        payload["audit_worker_model"] = central_runtime.normalize_codex_model(audit_model, label="audit model")
    payload["updated_at"] = utc_now()
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def describe_source(source: str) -> str:
    labels = {
        "env": MAX_WORKERS_ENV,
        "model_env": CODEX_MODEL_ENV,
        "saved_config": str(CONFIG_PATH),
        "default": "default",
    }
    return labels.get(source, source)


def resolve_max_workers() -> ResolvedMaxWorkers:
    env_value = env_max_workers()
    if env_value is not None:
        return ResolvedMaxWorkers(value=env_value, source="env")
    persisted = saved_max_workers()
    if persisted is not None:
        return ResolvedMaxWorkers(value=persisted, source="saved_config")
    return ResolvedMaxWorkers(value=DEFAULT_MAX_WORKERS, source="default")


def resolve_codex_model() -> ResolvedWorkerModel:
    return resolve_worker_model()


def resolve_worker_model() -> ResolvedWorkerModel:
    env_generic = env_worker_model()
    if env_generic is not None:
        return ResolvedWorkerModel(value=env_generic, source="model_env")
    env_value = env_codex_model()
    if env_value is not None:
        return ResolvedWorkerModel(value=env_value, source="model_env")
    persisted = saved_worker_model()
    if persisted is not None:
        return ResolvedWorkerModel(value=persisted, source="saved_config")
    return ResolvedWorkerModel(value=DEFAULT_CODEX_MODEL, source="default")


def runtime_status_payload() -> dict[str, object]:
    result = subprocess.run(runtime_cmd("status", "--json"), cwd=str(REPO_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        die((result.stderr or result.stdout or "dispatcher status failed").strip())
    raw = (result.stdout or "{}").strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"dispatcher status returned invalid JSON: {exc}")
        raise exc
    if not isinstance(payload, dict):
        die("dispatcher status returned a non-object payload")
    return payload


def launcher_status_payload() -> dict[str, object]:
    payload = runtime_status_payload()
    effective = resolve_max_workers()
    effective_model = resolve_worker_model()
    payload.update(
        {
            "launcher_config_path": str(CONFIG_PATH),
            "saved_max_workers": saved_max_workers(),
            "env_max_workers": env_max_workers(),
            "saved_default_worker_model": saved_worker_model() or saved_codex_model(),
            "saved_default_codex_model": saved_codex_model(),
            "saved_audit_worker_model": saved_audit_model(),
            "env_default_worker_model": env_worker_model() or env_codex_model(),
            # effective = what start/restart will use
            "effective_start_max_workers": effective.value,
            "effective_start_source": effective.source,
            "effective_start_default_worker_model": effective_model.value,
            "effective_start_default_worker_model_source": effective_model.source,
            "effective_start_default_codex_model": effective_model.value,
            "effective_start_default_codex_model_source": effective_model.source,
            # backward-compat aliases
            "next_start_max_workers": effective.value,
            "next_start_source": effective.source,
            "next_start_default_worker_model": effective_model.value,
            "next_start_default_worker_model_source": effective_model.source,
            "next_start_default_codex_model": effective_model.value,
            "next_start_default_codex_model_source": effective_model.source,
        }
    )
    return payload


def start_dispatcher(*, restart: bool = False) -> int:
    ensure_runtime()
    init_db()
    current = running_pid()
    resolved = resolve_max_workers()
    resolved_model = resolve_worker_model()
    effective_mode = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    effective_notify = saved_notify() or False
    validate_model_for_mode(resolved_model.value, effective_mode)
    if current and not restart:
        print(f"Dispatcher already running (pid {current})")
        print("Use 'config' to update settings, then 'restart' to apply them.")
        return print_status()
    if current and restart:
        stop_dispatcher(quiet=True)

    effective_audit_model = saved_audit_model()
    with LAUNCH_LOG_PATH.open("ab") as handle:
        daemon_args = [
            "daemon",
            "--max-workers", str(resolved.value),
            "--default-worker-model", resolved_model.value,
            "--worker-mode", effective_mode,
        ]
        if effective_audit_model:
            daemon_args.extend(["--audit-worker-model", effective_audit_model])
        if effective_notify:
            daemon_args.append("--notify")
        proc = subprocess.Popen(
            runtime_cmd(*daemon_args),
            cwd=str(REPO_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    deadline = time.time() + 10
    while time.time() < deadline:
        pid = running_pid()
        if pid:
            status = runtime_status_payload()
            verb = "restarted" if restart else "started"
            print(f"Dispatcher {verb} (pid {pid})")
            print(f"DB:  {DB_PATH or str(REPO_DIR / 'state' / 'central_tasks.db')}")
            print(f"Log: {LOG_PATH}")
            print(f"Max workers:   {status.get('configured_max_workers') or resolved.value}")
            print(f"Worker model:  {status.get('configured_default_worker_model') or resolved_model.value}")
            print(f"Audit model:   {effective_audit_model or '(same as worker)'}")
            print(f"Worker mode:   {status.get('worker_mode') or effective_mode}")
            return 0
        if proc.poll() is not None:
            die(tail_file(LAUNCH_LOG_PATH, 80) or "dispatcher failed to start")
        time.sleep(0.2)
    die(tail_file(LAUNCH_LOG_PATH, 80) or "dispatcher did not acquire lock in time")
    return 1


def print_status() -> int:
    ensure_runtime()
    init_db()
    print(json.dumps(launcher_status_payload(), indent=2, sort_keys=True))
    return 0


def stop_dispatcher(*, quiet: bool = False) -> int:
    ensure_runtime()
    result = subprocess.run(runtime_cmd("stop"), cwd=str(REPO_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        die((result.stderr or result.stdout or "dispatcher stop failed").strip())
    deadline = time.time() + 10
    while time.time() < deadline:
        if running_pid() is None:
            if not quiet:
                print("Dispatcher stopped")
            return 0
        time.sleep(0.2)
    die("dispatcher still appears to be running")
    return 1


def tail_file(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return f"{path}: no log yet"
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def _make_daemon_log():
    from central_runtime_v2.log import DaemonLog
    return DaemonLog(LOG_PATH)


def colorize_log_line(line: str) -> str:
    return _make_daemon_log().colorize_log_line(line)


def stream_colored_logs(path: Path, *, lines: int = 120) -> int:
    if not path.exists():
        print(f"{path}: no log yet")
        return 0
    daemon_log = _make_daemon_log()
    proc = subprocess.Popen(
        ["tail", "-n", str(lines), "-f", str(path)],
        cwd=str(REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            print(daemon_log.colorize_log_line(line.rstrip("\n")))
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=5)
        return 130
    return proc.wait()


def show_logs(follow: bool = False) -> int:
    ensure_runtime()
    daemon_log = _make_daemon_log()
    if follow:
        return stream_colored_logs(LOG_PATH)
    print("\n".join(daemon_log.colorize_log_line(line) for line in tail_file(LOG_PATH).splitlines()))
    return 0


def run_once() -> int:
    ensure_runtime()
    init_db()
    resolved_model = resolve_worker_model()
    effective_mode = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    result = subprocess.run(
        runtime_cmd("run-once", "--default-worker-model", resolved_model.value, "--worker-mode", effective_mode),
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        die((result.stderr or "dispatcher run-once failed").strip())
    return 0


def show_workers(*, as_json: bool, task_id: str | None, limit: int, recent_hours: float) -> int:
    ensure_runtime()
    init_db()
    command = ["worker-status", "--limit", str(limit), "--recent-hours", str(recent_hours)]
    if task_id:
        command.extend(["--task-id", task_id])
    if as_json:
        command.append("--json")
    result = subprocess.run(runtime_cmd(*command), cwd=str(REPO_DIR), capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        die((result.stderr or result.stdout or "dispatcher worker-status failed").strip())
    return 0


def show_config(*, max_workers: int | None = None, codex_model: str | None = None, worker_model: str | None = None, worker_mode: str | None = None, notify: bool | None = None, audit_model: str | None = None) -> int:
    ensure_runtime()
    if max_workers is not None or codex_model is not None or worker_model is not None or worker_mode is not None or notify is not None or audit_model is not None:
        save_config(max_workers=max_workers, codex_model=codex_model, worker_model=worker_model, worker_mode=worker_mode, notify=notify, audit_model=audit_model)
    payload = load_saved_config()
    effective = resolve_max_workers()
    effective_model = resolve_worker_model()
    print(
        json.dumps(
            {
                "config_path": str(CONFIG_PATH),
                "saved_max_workers": payload.get("max_workers"),
                "saved_default_worker_model": payload.get("default_worker_model"),
                "saved_default_codex_model": payload.get("default_codex_model"),
                "saved_audit_worker_model": payload.get("audit_worker_model"),
                "saved_worker_mode": payload.get("worker_mode"),
                "saved_notify": payload.get("notify"),
                "updated_at": payload.get("updated_at"),
                "env_max_workers": env_max_workers(),
                "env_default_worker_model": env_worker_model() or env_codex_model(),
                "effective_start_max_workers": effective.value,
                "effective_start_source": effective.source,
                "effective_start_default_worker_model": effective_model.value,
                "effective_start_default_worker_model_source": effective_model.source,
                "effective_start_default_codex_model": effective_model.value,
                "effective_start_default_codex_model_source": effective_model.source,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _effective_db_path() -> str:
    return DB_PATH or str(REPO_DIR / "state" / "central_tasks.db")


DEFAULT_REPO_MAX_CONCURRENT_WORKERS = 3


def _open_db_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(_effective_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def show_repo_config(*, repo: str | None = None, max_workers: int | None = None, as_json: bool = False) -> int:
    """Show or update per-repo concurrency caps."""
    conn = _open_db_rw()
    try:
        if max_workers is not None:
            if not repo:
                print("--repo is required when setting --max-workers", file=sys.stderr)
                return 1
            row = conn.execute("SELECT metadata_json FROM repos WHERE repo_id = ?", (repo,)).fetchone()
            if row is None:
                print(f"Repo not found: {repo!r}", file=sys.stderr)
                return 1
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            meta["max_concurrent_workers"] = max_workers
            conn.execute(
                "UPDATE repos SET metadata_json = ? WHERE repo_id = ?",
                (json.dumps(meta), repo),
            )
            conn.commit()
            print(f"Set {repo} max_concurrent_workers={max_workers}")

        where = "WHERE repo_id = ?" if repo else ""
        params = (repo,) if repo else ()
        rows = conn.execute(
            f"SELECT repo_id, metadata_json FROM repos {where} ORDER BY repo_id", params
        ).fetchall()

        result = []
        for row in rows:
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            cap = meta.get("max_concurrent_workers", DEFAULT_REPO_MAX_CONCURRENT_WORKERS)
            is_default = "max_concurrent_workers" not in meta
            result.append({
                "repo_id": row["repo_id"],
                "max_concurrent_workers": cap,
                "is_default": is_default,
            })

        if as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{'Repo':<24} {'Max Workers':<12} {'Source'}")
            print(f"{'----':<24} {'-----------':<12} {'------'}")
            for r in result:
                src = "default" if r["is_default"] else "configured"
                print(f"{r['repo_id']:<24} {r['max_concurrent_workers']:<12} {src}")
            print()
        return 0
    finally:
        conn.close()


def _run_self_check_stub() -> dict[str, object]:
    result = subprocess.run(
        [PYTHON_BIN, str(RUNTIME_SCRIPT), "self-check"],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "CENTRAL runtime self-check failed").strip()
        die(f"dispatcher self-check failed (exit {result.returncode}):\n{message}")
    raw = (result.stdout or "").strip()
    if not raw:
        die("dispatcher self-check returned no output")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"dispatcher self-check returned invalid JSON: {exc}")
        raise exc
    if not isinstance(payload, dict):
        die("dispatcher self-check returned a non-object payload")
    if payload.get("planner_status") != "done":
        die(
            "dispatcher self-check did not complete successfully: "
            f"planner_status={payload.get('planner_status')!r}"
        )
    runtime_status = payload.get("runtime_status")
    if runtime_status not in {"done", "pending_review"}:
        die(
            "dispatcher self-check runtime status is not successful: "
            f"runtime_status={runtime_status!r}"
        )
    last_runtime_error = payload.get("last_runtime_error")
    if last_runtime_error:
        die(f"dispatcher self-check reported runtime error: {last_runtime_error!r}")
    return payload


def run_check() -> int:
    validate_runtime_importable()
    effective_mode = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    resolved_model = resolve_worker_model()
    validate_model_for_mode(resolved_model.value, effective_mode)
    effort, effort_source = resolve_effort()
    max_workers = resolve_max_workers()
    effective_config = {
        "mode": effective_mode,
        "model": resolved_model.value,
        "effort": effort,
        "effort_source": effort_source,
        "max_workers": max_workers.value,
        "db_path": _effective_db_path(),
        "state_dir": str(STATE_DIR),
    }
    print("Effective configuration:")
    print(json.dumps(effective_config, indent=2, sort_keys=True))

    check_payload = _run_self_check_stub()
    print("Self-check result:")
    print(json.dumps(check_payload, indent=2, sort_keys=True))
    print("Dispatcher check passed")
    return 0


def kill_task(*, task_id: str, reason: str, as_json: bool) -> int:
    ensure_runtime()
    init_db()
    result = subprocess.run(
        db_cmd("operator-fail-task", "--task-id", task_id, "--reason", reason, "--actor-id", "dispatcher.operator", "--json"),
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die((result.stderr or result.stdout or "dispatcher kill-task failed").strip())
    raw = (result.stdout or "{}").strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"dispatcher kill-task returned invalid JSON: {exc}")
        raise exc
    if not isinstance(payload, dict):
        die("dispatcher kill-task returned a non-object payload")
    payload["worker_termination"] = terminate_worker(payload.get("kill_target"))
    snapshot = payload.get("snapshot") or payload  # structured response has snapshot key; flat fallback
    runtime = snapshot.get("runtime") or {}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"Task {task_id} failed by operator kill-task")
    print(f"Planner/runtime: {snapshot.get('planner_status')} / {runtime.get('runtime_status')}")
    termination = payload.get("worker_termination") or {}
    if termination.get("worker_present"):
        state = "terminated" if termination.get("terminated") else "still_running"
        print(f"Worker: {state} (pid {termination.get('pid')})")
    else:
        print("Worker: no active worker")
    print(f"Reason: {payload.get('reason')}")
    return 0


def prompt_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise MenuExitRequested from None


def prompt_with_default(label: str, default: str) -> str:
    raw = prompt_line(f"{label} [{default}]: ")
    return default if raw == "" else raw


def prompt_positive_int(label: str, default: int) -> int:
    while True:
        raw = prompt_with_default(label, str(default))
        try:
            return argparse_positive_int(raw)
        except argparse.ArgumentTypeError as exc:
            print(f"Invalid value: {exc}")


def prompt_optional_text(label: str, default: str) -> str:
    return prompt_with_default(label, default)


def prompt_yes_no(label: str, default: bool) -> bool:
    default_hint = "Y/n" if default else "y/N"
    while True:
        raw = prompt_line(f"{label} [{default_hint}]: ").lower()
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please enter y or n.")


def prompt_worker_mode(default: str) -> str:
    options = {"1": "codex", "2": "claude", "3": "gemini", "4": "grok", "5": "stub"}
    reverse = {value: key for key, value in options.items()}
    default_key = reverse.get(default, "1")
    while True:
        print("Worker provider/mode:")
        print("  1. codex")
        print("  2. claude")
        print("  3. gemini")
        print("  4. grok")
        print("  5. stub")
        raw = prompt_line(f"Select mode [{default_key}]: ")
        key = default_key if raw == "" else raw
        selected = options.get(key)
        if selected:
            return selected


def prompt_worker_model_select(label: str, default: str, mode: str) -> str:
    """Numbered selection for worker/audit model; never free-form."""
    if mode == "codex":
        choices = sorted(ALLOWED_CODEX_MODELS)
    elif mode == "gemini":
        choices = sorted(ALLOWED_GEMINI_MODELS)
    elif mode == "grok":
        choices = ALLOWED_GROK_MODELS_LIST
    else:
        choices = list(ALLOWED_CLAUDE_MODELS)
    numbered = {str(i + 1): m for i, m in enumerate(choices)}
    reverse = {m: str(i + 1) for i, m in enumerate(choices)}
    default_key = reverse.get(default, "1")
    while True:
        print(f"{label}:")
        for k, m in numbered.items():
            marker = " *" if m == default else ""
            print(f"  {k}. {m}{marker}")
        raw = prompt_line(f"Select [{default_key}]: ")
        key = default_key if raw == "" else raw
        selected = numbered.get(key)
        if selected:
            return selected
        print(f"Enter a number 1–{len(choices)}.")
        print("Invalid selection.")


def print_menu_header() -> None:
    running = running_pid()
    effective_workers = resolve_max_workers()
    effective_model = resolve_worker_model()
    mode = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    notify_enabled = saved_notify() or False
    print()
    print("=== Dispatcher Menu ===")
    print(f"Repo: {REPO_DIR}")
    print(f"State: {STATE_DIR}")
    print(f"Running: {'yes (pid ' + str(running) + ')' if running else 'no'}")
    print(
        "Saved config: "
        f"workers={effective_workers.value} model={effective_model.value} mode={mode} notify={notify_enabled}"
    )
    print()
    print("  1. Start dispatcher")
    print("  2. Stop dispatcher")
    print("  3. Restart dispatcher")
    print("  4. Update saved config")
    print("  5. Show status")
    print("  6. Show workers")
    print("  7. Show logs")
    print("  8. Follow logs")
    print("  9. Run once")
    print(" 10. Run check")
    print(" 11. Kill task")
    print(" 12. Repo concurrency caps")
    print("  0. Exit")
    print()


def run_start_or_restart(*, restart: bool) -> int:
    workers_default = resolve_max_workers().value
    model_default = resolve_worker_model().value
    mode_default = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    audit_default = saved_audit_model() or model_default
    notify_default = saved_notify() or False

    max_workers = prompt_positive_int("Max workers", workers_default)
    worker_mode = prompt_worker_mode(mode_default)
    worker_model = prompt_worker_model_select("Implementor model", model_default, worker_mode)
    audit_model = prompt_worker_model_select("Audit model", audit_default, worker_mode)
    notify = prompt_yes_no("Enable notifications", notify_default)
    # Save ALL config before starting — config is the single source of truth
    save_config(
        max_workers=max_workers,
        worker_model=worker_model,
        worker_mode=worker_mode,
        notify=notify,
        audit_model=audit_model,
    )
    return start_dispatcher(restart=restart)


def run_config_update() -> int:
    workers_default = saved_max_workers() or resolve_max_workers(None, restart=False).value
    model_default = saved_worker_model() or resolve_worker_model(None, restart=False).value
    mode_default = saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    audit_default = saved_audit_model() or model_default
    notify_default = saved_notify() or False

    max_workers = prompt_positive_int("Saved max workers", workers_default)
    worker_mode = prompt_worker_mode(mode_default)
    worker_model = prompt_worker_model_select("Implementor model", model_default, worker_mode)
    audit_model = prompt_worker_model_select("Audit model", audit_default, worker_mode)
    notify = prompt_yes_no("Saved notify default", notify_default)
    rc = show_config(
        max_workers=max_workers,
        worker_model=worker_model,
        worker_mode=worker_mode,
        notify=notify,
        audit_model=audit_model,
    )

    # Per-repo concurrency caps
    print()
    show_repo_config()
    while True:
        repo = prompt_line("Set cap for repo (leave blank to skip): ").strip()
        if not repo:
            break
        cap = prompt_positive_int(f"Max concurrent workers for {repo!r}", DEFAULT_REPO_MAX_CONCURRENT_WORKERS)
        show_repo_config(repo=repo, max_workers=cap)

    return rc


def run_workers_prompt() -> int:
    as_json = prompt_yes_no("Output JSON", False)
    task_id = prompt_line("Filter by task id (optional): ")
    limit = prompt_positive_int("Rows limit", 5)
    recent_hours_raw = prompt_with_default("Recent hours", "24")
    try:
        recent_hours = float(recent_hours_raw)
    except ValueError:
        print(f"Invalid recent hours: {recent_hours_raw!r}")
        return 1
    return show_workers(as_json=as_json, task_id=(task_id or None), limit=limit, recent_hours=recent_hours)


def run_kill_task_prompt() -> int:
    task_id = prompt_line("Task id: ")
    if not task_id:
        print("Task id is required.")
        return 1
    reason = prompt_with_default("Reason", "operator kill requested")
    as_json = prompt_yes_no("Output JSON", False)
    return kill_task(task_id=task_id, reason=reason, as_json=as_json)


def run_menu() -> int:
    ensure_runtime()
    init_db()
    try:
        while True:
            print_menu_header()
            choice = prompt_line("Select action [0]: ") or "0"
            if choice == "0":
                print("Exiting dispatcher menu.")
                return 0
            if choice == "1":
                run_start_or_restart(restart=False)
                continue
            if choice == "2":
                stop_dispatcher()
                continue
            if choice == "3":
                run_start_or_restart(restart=True)
                continue
            if choice == "4":
                run_config_update()
                continue
            if choice == "5":
                print_status()
                continue
            if choice == "6":
                run_workers_prompt()
                continue
            if choice == "7":
                show_logs(follow=False)
                continue
            if choice == "8":
                show_logs(follow=True)
                continue
            if choice == "9":
                run_once()
                continue
            if choice == "10":
                run_check()
                continue
            if choice == "11":
                run_kill_task_prompt()
                continue
            if choice == "12":
                show_repo_config()
                continue
            print(f"Invalid menu option: {choice!r}")
    except MenuExitRequested:
        print("Exiting dispatcher menu.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CENTRAL dispatcher operator wrapper")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("start", help="Start the dispatcher daemon (uses saved config)")
    subparsers.add_parser("restart", help="Stop and restart the dispatcher (applies saved config)")

    subparsers.add_parser("stop", help="Stop the dispatcher daemon")
    subparsers.add_parser("status", help="Show dispatcher status")
    workers_parser = subparsers.add_parser("workers", help="Inspect active and recent workers")
    workers_parser.add_argument("--json", action="store_true")
    workers_parser.add_argument("--task-id")
    workers_parser.add_argument("--limit", type=argparse_positive_int, default=5)
    workers_parser.add_argument("--recent-hours", type=float, default=24.0)
    worker_status_parser = subparsers.add_parser("worker-status", help="Inspect active and recent workers")
    worker_status_parser.add_argument("--json", action="store_true")
    worker_status_parser.add_argument("--task-id")
    worker_status_parser.add_argument("--limit", type=argparse_positive_int, default=5)
    worker_status_parser.add_argument("--recent-hours", type=float, default=24.0)
    subparsers.add_parser("logs", help="Show recent dispatcher logs")
    subparsers.add_parser("follow", help="Follow dispatcher logs")
    subparsers.add_parser("once", help="Run one dispatcher cycle")
    subparsers.add_parser("run-once", help="Run one dispatcher cycle")
    subparsers.add_parser("run_once", help="Run one dispatcher cycle")
    subparsers.add_parser("check", help="Validate config and run a stub self-check")
    subparsers.add_parser("menu", help="Open interactive dispatcher menu")

    config_parser = subparsers.add_parser("config", help="Show or update persisted launcher defaults")
    config_parser.add_argument("--max-workers", type=argparse_positive_int)
    config_parser.add_argument("--codex-model", help="(deprecated, use --worker-model)")
    config_parser.add_argument("--worker-model")
    config_parser.add_argument("--audit-model", help="Separate model for audit tasks (cross-model auditing)")
    config_parser.add_argument("--worker-mode", choices=["codex", "claude", "gemini", "grok", "stub"])
    config_parser.add_argument("--notify", action="store_true", default=None)
    config_parser.add_argument("--no-notify", dest="notify", action="store_false")

    kill_parser = subparsers.add_parser("kill-task", help="Fail a task by operator request and terminate its worker if present")
    kill_parser.add_argument("task_id")
    kill_parser.add_argument("--reason", default="operator kill requested")
    kill_parser.add_argument("--json", action="store_true")

    repo_config_parser = subparsers.add_parser("repo-config", help="Show or set per-repo concurrency caps")
    repo_config_parser.add_argument("--repo", help="Repo ID to filter or update")
    repo_config_parser.add_argument("--max-workers", type=int, help="Max concurrent workers for this repo")
    repo_config_parser.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    cmd = args.command or "start"
    if cmd == "start":
        return start_dispatcher(restart=False)
    if cmd == "restart":
        return start_dispatcher(restart=True)
    if cmd == "stop":
        return stop_dispatcher()
    if cmd == "status":
        return print_status()
    if cmd in {"workers", "worker-status"}:
        return show_workers(
            as_json=getattr(args, "json", False),
            task_id=getattr(args, "task_id", None),
            limit=getattr(args, "limit", 5),
            recent_hours=getattr(args, "recent_hours", 24.0),
        )
    if cmd == "logs":
        return show_logs(follow=False)
    if cmd == "follow":
        return show_logs(follow=True)
    if cmd in {"once", "run-once", "run_once"}:
        return run_once()
    if cmd == "check":
        return run_check()
    if cmd == "menu":
        return run_menu()
    if cmd == "config":
        return show_config(
            max_workers=getattr(args, "max_workers", None),
            codex_model=getattr(args, "codex_model", None),
            worker_model=getattr(args, "worker_model", None),
            worker_mode=getattr(args, "worker_mode", None),
            notify=getattr(args, "notify", None),
            audit_model=getattr(args, "audit_model", None),
        )
    if cmd == "kill-task":
        return kill_task(task_id=args.task_id, reason=args.reason, as_json=getattr(args, "json", False))
    if cmd == "repo-config":
        return show_repo_config(
            repo=getattr(args, "repo", None),
            max_workers=getattr(args, "max_workers", None),
            as_json=getattr(args, "json", False),
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
