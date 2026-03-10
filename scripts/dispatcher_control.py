#!/usr/bin/env python3
"""Operator wrapper for the autonomy dispatcher."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


REPO_DIR = Path("/home/cobra/photo_auto_tagging")
VENV_PYTHON = REPO_DIR / ".venv" / "bin" / "python"
AUTONOMY_BIN = REPO_DIR / ".venv" / "bin" / "autonomy"
PROFILE = os.environ.get("AUTONOMY_PROFILE", "default")
PROFILE_DIR = Path.home() / ".autonomy" / "profiles" / PROFILE
STATE_DIR = PROFILE_DIR / ".worker-state"
LOCK_PATH = STATE_DIR / "dispatcher.lock"
LOG_PATH = STATE_DIR / "dispatcher.log"
LAUNCH_LOG_PATH = STATE_DIR / "dispatcher-launcher.log"


def die(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ensure_runtime() -> None:
    if not REPO_DIR.is_dir():
        die(f"photo_auto_tagging repo missing: {REPO_DIR}")
    if not VENV_PYTHON.exists():
        die(f"dispatcher runtime missing: {VENV_PYTHON}")


def autonomy_cmd(*args: str) -> list[str]:
    if AUTONOMY_BIN.exists():
        return [str(AUTONOMY_BIN), *args]
    return [str(VENV_PYTHON), "-m", "autonomy.cli", *args]


def autonomy_exec() -> str:
    return str(AUTONOMY_BIN if AUTONOMY_BIN.exists() else VENV_PYTHON)


def run_capture(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        autonomy_cmd(*args),
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
        check=check,
    )


def init_profile() -> None:
    result = run_capture("init", "--profile", PROFILE)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if result.stdout.strip():
        # Keep init quiet unless there is a real failure path later.
        pass


def lock_payload() -> dict[str, object] | None:
    if not LOCK_PATH.exists():
        return None
    try:
        payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running_pid() -> int | None:
    payload = lock_payload()
    if not payload:
        return None
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def print_status() -> int:
    init_profile()
    result = run_capture("dispatch", "status", "--profile", PROFILE, check=False)
    if result.returncode == 0:
        stdout = result.stdout.strip()
        if stdout:
            print(stdout)
        else:
            print("{}")
        return 0
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    die(stderr or stdout or "dispatcher status failed")
    return 1


def start_dispatcher(*, restart: bool = False) -> int:
    ensure_runtime()
    init_profile()
    current_pid = running_pid()
    if current_pid and not restart:
        print(f"Dispatcher already running (pid {current_pid})")
        return print_status()
    if current_pid and restart:
        stop_dispatcher(quiet=True)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LAUNCH_LOG_PATH.open("ab") as log_handle:
        proc = subprocess.Popen(
            autonomy_cmd("dispatch", "daemon", "--profile", PROFILE),
            cwd=str(REPO_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    deadline = time.time() + 10
    while time.time() < deadline:
        pid = running_pid()
        if pid:
            print(f"Dispatcher started (pid {pid})")
            print(f"Profile: {PROFILE}")
            print(f"Log:     {LOG_PATH}")
            return 0
        if proc.poll() is not None:
            launch_log = tail_file(LAUNCH_LOG_PATH, 80)
            die(f"dispatcher failed to start\n{launch_log}".rstrip())
        time.sleep(0.2)

    launch_log = tail_file(LAUNCH_LOG_PATH, 80)
    die(f"dispatcher did not acquire lock in time\n{launch_log}".rstrip())
    return 1


def stop_dispatcher(*, quiet: bool = False) -> int:
    ensure_runtime()
    init_profile()
    result = run_capture("dispatch", "stop", "--profile", PROFILE, check=False)
    message = (result.stdout or result.stderr or "").strip() or "stop issued"
    deadline = time.time() + 10
    while time.time() < deadline:
        if running_pid() is None:
            if not quiet:
                print("Dispatcher stopped")
            return 0
        time.sleep(0.2)
    die(f"{message}\ndispatcher still appears to be running")
    return 1


def tail_file(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return f"{path}: no log yet"
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def show_logs(follow: bool = False) -> int:
    ensure_runtime()
    init_profile()
    if follow:
        os.execv(autonomy_exec(), autonomy_cmd("dispatch", "tail", "--profile", PROFILE, "--follow"))
    print(tail_file(LOG_PATH))
    return 0


def run_once() -> int:
    ensure_runtime()
    init_profile()
    result = run_capture("dispatch", "run-once", "--profile", PROFILE, check=False)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        die((result.stderr or "").strip() or "dispatch run-once failed")
    return 0


def usage() -> int:
    print("Usage: dispatcher [start|restart|stop|status|logs|follow|once]")
    return 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "start"
    if cmd == "start":
        return start_dispatcher(restart=False)
    if cmd == "restart":
        return start_dispatcher(restart=True)
    if cmd == "stop":
        return stop_dispatcher()
    if cmd == "status":
        return print_status()
    if cmd == "logs":
        return show_logs(follow=False)
    if cmd == "follow":
        return show_logs(follow=True)
    if cmd in {"once", "run-once", "run_once"}:
        return run_once()
    return usage()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
