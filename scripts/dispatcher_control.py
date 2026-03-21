#!/usr/bin/env python3
"""Operator wrapper for the CENTRAL-native dispatcher."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import central_runtime_v2 as central_runtime
from central_runtime_v2.config import ALLOWED_CODEX_MODELS, ALLOWED_REASONING_EFFORTS

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


def save_config(*, max_workers: int | None = None, codex_model: str | None = None, worker_model: str | None = None, worker_mode: str | None = None, notify: bool | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = load_saved_config()
    if max_workers is not None:
        payload["max_workers"] = max_workers
    if worker_model is not None:
        payload["default_worker_model"] = central_runtime.normalize_codex_model(worker_model, label="worker model")
    elif codex_model is not None:
        payload["default_worker_model"] = central_runtime.normalize_codex_model(codex_model, label="codex model")
    if worker_mode is not None:
        if worker_mode not in ("codex", "claude", "stub"):
            die(f"invalid worker mode: {worker_mode}")
        payload["worker_mode"] = worker_mode
    if notify is not None:
        payload["notify"] = notify
    payload["updated_at"] = utc_now()
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def describe_source(source: str) -> str:
    labels = {
        "cli": "cli flag",
        "env": MAX_WORKERS_ENV,
        "model_env": CODEX_MODEL_ENV,
        "running_daemon": "running daemon",
        "saved_config": str(CONFIG_PATH),
        "default": "default",
    }
    return labels.get(source, source)


def resolve_max_workers(cli_value: int | None, *, restart: bool) -> ResolvedMaxWorkers:
    if cli_value is not None:
        return ResolvedMaxWorkers(value=cli_value, source="cli")
    env_value = env_max_workers()
    if env_value is not None:
        return ResolvedMaxWorkers(value=env_value, source="env")
    if restart:
        payload = running_lock_payload() or {}
        running_value = payload.get("max_workers")
        if running_value is not None:
            return ResolvedMaxWorkers(
                value=parse_positive_int(str(running_value), label="running dispatcher max_workers"),
                source="running_daemon",
            )
    persisted = saved_max_workers()
    if persisted is not None:
        return ResolvedMaxWorkers(value=persisted, source="saved_config")
    return ResolvedMaxWorkers(value=DEFAULT_MAX_WORKERS, source="default")


def resolve_codex_model(cli_value: str | None, *, restart: bool) -> ResolvedWorkerModel:
    return resolve_worker_model(cli_value, restart=restart)


def resolve_worker_model(cli_value: str | None, *, restart: bool) -> ResolvedWorkerModel:
    if cli_value is not None:
        return ResolvedWorkerModel(
            value=central_runtime.normalize_codex_model(cli_value, label="worker model"),
            source="cli",
        )
    # Check generic env var first, then codex-specific
    env_generic = env_worker_model()
    if env_generic is not None:
        return ResolvedWorkerModel(value=env_generic, source="model_env")
    env_value = env_codex_model()
    if env_value is not None:
        return ResolvedWorkerModel(value=env_value, source="model_env")
    if restart:
        payload = running_lock_payload() or {}
        running_value = payload.get("default_worker_model") or payload.get("default_codex_model")
        if running_value is not None:
            return ResolvedWorkerModel(
                value=central_runtime.normalize_codex_model(running_value, label="running dispatcher worker model"),
                source="running_daemon",
            )
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
    next_start = resolve_max_workers(None, restart=False)
    next_restart = resolve_max_workers(None, restart=True)
    next_start_model = resolve_codex_model(None, restart=False)
    next_restart_model = resolve_codex_model(None, restart=True)
    payload.update(
        {
            "launcher_config_path": str(CONFIG_PATH),
            "saved_max_workers": saved_max_workers(),
            "env_max_workers": env_max_workers(),
            "next_start_max_workers": next_start.value,
            "next_start_source": next_start.source,
            "next_restart_max_workers": next_restart.value,
            "next_restart_source": next_restart.source,
            "saved_default_worker_model": saved_worker_model() or saved_codex_model(),
            "saved_default_codex_model": saved_codex_model(),
            "env_default_worker_model": env_worker_model() or env_codex_model(),
            "env_default_codex_model": env_codex_model(),
            "next_start_default_worker_model": next_start_model.value,
            "next_start_default_worker_model_source": next_start_model.source,
            "next_start_default_codex_model": next_start_model.value,
            "next_start_default_codex_model_source": next_start_model.source,
            "next_restart_default_worker_model": next_restart_model.value,
            "next_restart_default_worker_model_source": next_restart_model.source,
            "next_restart_default_codex_model": next_restart_model.value,
            "next_restart_default_codex_model_source": next_restart_model.source,
        }
    )
    return payload


def start_dispatcher(*, restart: bool = False, max_workers: int | None = None, codex_model: str | None = None, worker_model: str | None = None, worker_mode: str | None = None, notify: bool | None = None) -> int:
    ensure_runtime()
    init_db()
    current = running_pid()
    resolved = resolve_max_workers(max_workers, restart=restart)
    resolved_model = resolve_worker_model(worker_model or codex_model, restart=restart)
    effective_mode = worker_mode or saved_worker_mode() or os.environ.get("CENTRAL_WORKER_MODE", "codex")
    effective_notify = notify if notify is not None else (saved_notify() or False)
    validate_model_for_mode(resolved_model.value, effective_mode)
    if current and not restart:
        if max_workers is not None:
            print("Dispatcher already running; restart is required to apply a new max worker limit.")
        if codex_model is not None or worker_model is not None:
            print("Dispatcher already running; restart is required to apply a new default model.")
        print(f"Dispatcher already running (pid {current})")
        return print_status()
    if current and restart:
        stop_dispatcher(quiet=True)

    with LAUNCH_LOG_PATH.open("ab") as handle:
        daemon_args = [
            "daemon",
            "--max-workers", str(resolved.value),
            "--default-worker-model", resolved_model.value,
            "--worker-mode", effective_mode,
        ]
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
            print(f"Dispatcher started (pid {pid})")
            print(f"DB:  {DB_PATH or str(REPO_DIR / 'state' / 'central_tasks.db')}")
            print(f"Log: {LOG_PATH}")
            print(
                "Max workers: "
                f"{status.get('configured_max_workers') or resolved.value} "
                f"(source: {describe_source(resolved.source)})"
            )
            print(
                "Default model: "
                f"{status.get('configured_default_worker_model') or status.get('configured_default_codex_model') or resolved_model.value} "
                f"(source: {describe_source(resolved_model.source)})"
            )
            print(f"Worker mode: {status.get('worker_mode') or effective_mode}")
            if saved_max_workers() is not None:
                print(f"Saved default: {saved_max_workers()} ({CONFIG_PATH})")
            if saved_worker_model() is not None:
                print(f"Saved model: {saved_worker_model()} ({CONFIG_PATH})")
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
    resolved_model = resolve_worker_model(None, restart=False)
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


def show_config(*, max_workers: int | None = None, codex_model: str | None = None, worker_model: str | None = None, worker_mode: str | None = None, notify: bool | None = None) -> int:
    ensure_runtime()
    if max_workers is not None or codex_model is not None or worker_model is not None or worker_mode is not None or notify is not None:
        save_config(max_workers=max_workers, codex_model=codex_model, worker_model=worker_model, worker_mode=worker_mode, notify=notify)
    payload = load_saved_config()
    effective_model = resolve_worker_model(None, restart=False)
    print(
        json.dumps(
            {
                "config_path": str(CONFIG_PATH),
                "saved_max_workers": payload.get("max_workers"),
                "saved_default_worker_model": payload.get("default_worker_model"),
                "saved_default_codex_model": payload.get("default_codex_model"),
                "saved_worker_mode": payload.get("worker_mode"),
                "saved_notify": payload.get("notify"),
                "updated_at": payload.get("updated_at"),
                "env_max_workers": env_max_workers(),
                "env_default_worker_model": env_worker_model() or env_codex_model(),
                "env_default_codex_model": env_codex_model(),
                "effective_start_max_workers": resolve_max_workers(None, restart=False).value,
                "effective_start_source": resolve_max_workers(None, restart=False).source,
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
    resolved_model = resolve_worker_model(None, restart=False)
    validate_model_for_mode(resolved_model.value, effective_mode)
    effort, effort_source = resolve_effort()
    max_workers = resolve_max_workers(None, restart=False)
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
    snapshot = payload.get("snapshot") or {}
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CENTRAL dispatcher operator wrapper")
    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser("start", help="Start the dispatcher daemon")
    start_parser.add_argument("--max-workers", type=argparse_positive_int)
    start_parser.add_argument("--codex-model", help="(deprecated, use --worker-model)")
    start_parser.add_argument("--worker-model")
    start_parser.add_argument("--worker-mode", choices=["codex", "claude", "stub"])
    start_parser.add_argument("--notify", action="store_true", default=None)
    start_parser.add_argument("--no-notify", dest="notify", action="store_false")

    restart_parser = subparsers.add_parser("restart", help="Restart the dispatcher daemon")
    restart_parser.add_argument("--max-workers", type=argparse_positive_int)
    restart_parser.add_argument("--codex-model", help="(deprecated, use --worker-model)")
    restart_parser.add_argument("--worker-model")
    restart_parser.add_argument("--worker-mode", choices=["codex", "claude", "stub"])
    restart_parser.add_argument("--notify", action="store_true", default=None)
    restart_parser.add_argument("--no-notify", dest="notify", action="store_false")

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

    config_parser = subparsers.add_parser("config", help="Show or update persisted launcher defaults")
    config_parser.add_argument("--max-workers", type=argparse_positive_int)
    config_parser.add_argument("--codex-model", help="(deprecated, use --worker-model)")
    config_parser.add_argument("--worker-model")
    config_parser.add_argument("--worker-mode", choices=["codex", "claude", "stub"])
    config_parser.add_argument("--notify", action="store_true", default=None)
    config_parser.add_argument("--no-notify", dest="notify", action="store_false")

    kill_parser = subparsers.add_parser("kill-task", help="Fail a task by operator request and terminate its worker if present")
    kill_parser.add_argument("task_id")
    kill_parser.add_argument("--reason", default="operator kill requested")
    kill_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    cmd = args.command or "start"
    if cmd == "start":
        return start_dispatcher(
            restart=False,
            max_workers=getattr(args, "max_workers", None),
            codex_model=getattr(args, "codex_model", None),
            worker_model=getattr(args, "worker_model", None),
            worker_mode=getattr(args, "worker_mode", None),
            notify=getattr(args, "notify", None),
        )
    if cmd == "restart":
        return start_dispatcher(
            restart=True,
            max_workers=getattr(args, "max_workers", None),
            codex_model=getattr(args, "codex_model", None),
            worker_model=getattr(args, "worker_model", None),
            worker_mode=getattr(args, "worker_mode", None),
            notify=getattr(args, "notify", None),
        )
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
    if cmd == "config":
        return show_config(
            max_workers=getattr(args, "max_workers", None),
            codex_model=getattr(args, "codex_model", None),
            worker_model=getattr(args, "worker_model", None),
            worker_mode=getattr(args, "worker_mode", None),
            notify=getattr(args, "notify", None),
        )
    if cmd == "kill-task":
        return kill_task(task_id=args.task_id, reason=args.reason, as_json=getattr(args, "json", False))
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
