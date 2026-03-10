#!/usr/bin/env python3
"""Start, stop, and proxy-run aimSoloAnalysis from WSL2."""

from __future__ import annotations

import http.client
import os
import re
import signal
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit


REPO_DIR = Path("/home/cobra/aimSoloAnalysis")
STATE_DIR = Path.home() / ".local" / "state" / "aimsolo"
BACKEND_PORT = 8000
DEFAULT_UI_PORT = 5173
BACKEND_PID = STATE_DIR / "backend.pid"
UI_PID = STATE_DIR / "ui.pid"
UI_PORT_FILE = STATE_DIR / "ui_port"
BACKEND_LOG = STATE_DIR / "backend.log"
UI_LOG = STATE_DIR / "ui.log"


def die(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ensure_repo() -> None:
    if not REPO_DIR.is_dir():
        die(f"aimSoloAnalysis repo missing: {REPO_DIR}")


def ui_root() -> Path:
    for candidate in (REPO_DIR / "ui-v2", REPO_DIR / "ui"):
        if candidate.is_dir():
            return candidate
    die(f"No UI directory found in {REPO_DIR}")


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_runtime_env() -> Path:
    ensure_repo()
    ensure_state_dir()
    venv_dir = REPO_DIR / ".venv-tests"
    python_bin = venv_dir / "bin" / "python"
    pip_bin = venv_dir / "bin" / "pip"

    if not python_bin.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    missing = []
    checks = {
        "fastapi": "fastapi",
        "pydantic": "pydantic",
        "uvicorn": "uvicorn",
    }
    for module_name, package_name in checks.items():
        result = subprocess.run(
            [str(python_bin), "-c", f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec('{module_name}') else 1)"],
            check=False,
        )
        if result.returncode != 0:
            missing.append(package_name)

    if missing:
        subprocess.run([str(pip_bin), "install", *missing], check=True)

    return python_bin


def write_pid(path: Path, pid: int) -> None:
    path.write_text(f"{pid}\n", encoding="utf-8")


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def read_ui_port() -> int:
    try:
        value = int(UI_PORT_FILE.read_text(encoding="utf-8").strip())
        if 1 <= value <= 65535:
            return value
    except (FileNotFoundError, ValueError):
        pass
    return DEFAULT_UI_PORT


def pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_pid(pid: int | None, *, label: str) -> None:
    if not pid or not pid_is_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        if not pid_is_running(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def command_for_pid(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def listener_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []
    pids: list[int] = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if raw.isdigit():
            pids.append(int(raw))
    return pids


def is_aim_backend_process(command: str) -> bool:
    return "uvicorn api.app:app" in command and str(REPO_DIR) in command


def is_aim_ui_process(command: str) -> bool:
    return "aim_control.py __serve_ui" in command


def stop_port_listeners(port: int, matcher) -> None:
    for pid in listener_pids(port):
        command = command_for_pid(pid)
        if matcher(command):
            kill_pid(pid, label=f"port {port}")


def wait_for_port(port: int, *, host: str = "127.0.0.1", timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    die(f"Timed out waiting for {host}:{port}")


def wsl_ip() -> str | None:
    try:
        output = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return None
    for token in output.split():
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
            return token
    return None


def open_browser(url: str) -> None:
    if os.environ.get("AIM_NO_BROWSER") == "1":
        return
    subprocess.run(["cmd.exe", "/C", "start", "", url], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_stack(*, quiet: bool = False) -> None:
    backend_pid = read_pid(BACKEND_PID)
    ui_pid = read_pid(UI_PID)
    kill_pid(ui_pid, label="UI")
    kill_pid(backend_pid, label="backend")
    stop_port_listeners(BACKEND_PORT, is_aim_backend_process)
    stop_port_listeners(read_ui_port(), is_aim_ui_process)
    for path in (BACKEND_PID, UI_PID, UI_PORT_FILE):
        path.unlink(missing_ok=True)
    if not quiet:
        print("Aim Solo stopped")


def spawn_process(args: list[str], *, cwd: Path, log_path: Path) -> int:
    with log_path.open("ab") as log_handle:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


class AimUIHandler(BaseHTTPRequestHandler):
    server_version = "AimUIProxy/1.0"

    def do_HEAD(self) -> None:
        self.handle_request()

    def do_GET(self) -> None:
        self.handle_request()

    def do_POST(self) -> None:
        self.handle_request()

    def do_PUT(self) -> None:
        self.handle_request()

    def do_PATCH(self) -> None:
        self.handle_request()

    def do_DELETE(self) -> None:
        self.handle_request()

    def do_OPTIONS(self) -> None:
        self.handle_request()

    def log_message(self, format: str, *args: object) -> None:
        return

    def handle_request(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.serve_index()
            return
        if parsed.path == "/healthz":
            self.send_text(200, "ok")
            return
        if parsed.path.startswith("/api/") or parsed.path == "/api":
            self.proxy_api()
            return
        self.serve_static(parsed.path)

    def serve_index(self) -> None:
        html_path = ui_root() / "index.html"
        html = html_path.read_text(encoding="utf-8")
        api_base = "window.location.origin + '/api'"
        html = html.replace(
            '<meta name="api-base" content="http://localhost:8000" />',
            f'<meta name="api-base" content="" />\n  <script>window.AIMSOLO_API_BASE = {api_base};</script>',
            1,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def serve_static(self, raw_path: str) -> None:
        root = ui_root().resolve()
        relative = raw_path.lstrip("/") or "index.html"
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(target))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def proxy_api(self) -> None:
        path = self.path[4:] if self.path.startswith("/api") else self.path
        if not path:
            path = "/"
        body = None
        content_length = self.headers.get("Content-Length")
        if content_length:
            body = self.rfile.read(int(content_length))
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "accept-encoding", "content-length"}
        }
        conn = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=60)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
        except OSError as exc:
            self.send_text(502, f"Backend unavailable: {exc}")
            return
        finally:
            conn.close()

        self.send_response(response.status)
        for key, value in response.getheaders():
            lower = key.lower()
            if lower in {"connection", "transfer-encoding", "content-length"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")


def serve_ui(port: int) -> None:
    ensure_repo()
    server = ThreadingHTTPServer(("0.0.0.0", port), AimUIHandler)
    server.serve_forever()


def tail_file(path: Path, *, lines: int = 80) -> str:
    if not path.exists():
        return f"{path}: no log yet"
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def start_stack(ui_port: int) -> None:
    if not (1 <= ui_port <= 65535):
        die(f"Invalid UI port: {ui_port}")
    python_bin = ensure_runtime_env()
    stop_stack(quiet=True)

    backend_pid = spawn_process(
        [str(python_bin), "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        cwd=REPO_DIR,
        log_path=BACKEND_LOG,
    )
    write_pid(BACKEND_PID, backend_pid)
    wait_for_port(BACKEND_PORT)

    ui_pid = spawn_process(
        [sys.executable, str(Path(__file__).resolve()), "__serve_ui", str(ui_port)],
        cwd=REPO_DIR,
        log_path=UI_LOG,
    )
    write_pid(UI_PID, ui_pid)
    UI_PORT_FILE.write_text(f"{ui_port}\n", encoding="utf-8")
    wait_for_port(ui_port)
    browser_url = f"http://127.0.0.1:{ui_port}"
    wsl_url = f"http://{wsl_ip()}:{ui_port}" if wsl_ip() else None
    open_browser(browser_url)

    print(f"Aim Solo started")
    print(f"UI:      {browser_url}")
    if wsl_url:
        print(f"WSL IP:  {wsl_url}")
    print(f"API:     http://localhost:{BACKEND_PORT}")
    print(f"Logs:    {UI_LOG} | {BACKEND_LOG}")


def status() -> None:
    ui_port = read_ui_port()
    backend_pid = read_pid(BACKEND_PID)
    ui_pid = read_pid(UI_PID)
    backend_running = pid_is_running(backend_pid)
    ui_running = pid_is_running(ui_pid)
    if backend_running or ui_running:
        browser_url = f"http://127.0.0.1:{ui_port}"
        wsl_url = f"http://{wsl_ip()}:{ui_port}" if wsl_ip() else None
        print("Aim Solo status: running")
        print(f"UI:      {'up' if ui_running else 'down'} on {browser_url} (pid {ui_pid or 'n/a'})")
        if wsl_url:
            print(f"WSL IP:  {wsl_url}")
        print(f"API:     {'up' if backend_running else 'down'} on http://localhost:{BACKEND_PORT} (pid {backend_pid or 'n/a'})")
        print(f"Logs:    {UI_LOG} | {BACKEND_LOG}")
    else:
        print("Aim Solo status: stopped")


def logs() -> None:
    print("== UI log ==")
    print(tail_file(UI_LOG))
    print("")
    print("== Backend log ==")
    print(tail_file(BACKEND_LOG))


def parse_args(argv: Iterable[str]) -> tuple[str, int]:
    args = list(argv)
    if not args:
        return ("restart", DEFAULT_UI_PORT)
    if args[0] == "__serve_ui":
        if len(args) != 2 or not args[1].isdigit():
            die("internal usage error", 2)
        return ("__serve_ui", int(args[1]))
    if len(args) == 1 and args[0].isdigit():
        return ("restart", int(args[0]))

    action = args[0]
    ui_port = read_ui_port()
    if len(args) >= 2:
        if args[1].isdigit():
            ui_port = int(args[1])
        else:
            die(f"Invalid UI port: {args[1]}")
    return (action, ui_port)


def main(argv: list[str]) -> int:
    action, ui_port = parse_args(argv[1:])
    if action == "__serve_ui":
        serve_ui(ui_port)
        return 0
    if action in {"start", "restart"}:
        start_stack(ui_port)
        return 0
    if action == "stop":
        stop_stack()
        return 0
    if action == "status":
        status()
        return 0
    if action == "logs":
        logs()
        return 0
    die("Usage: aim [start|restart|stop|status|logs] [ui_port]\n       aim [ui_port]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
