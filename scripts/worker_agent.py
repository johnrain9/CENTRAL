#!/usr/bin/env python3
"""Remote worker agent daemon for CENTRAL dispatcher.

Runs on a remote machine, polls the dispatcher coordination API for tasks,
executes them locally using the same backend infrastructure as the local
dispatcher, and ships results back via HTTP.

Usage:
    python3 scripts/worker_agent.py \\
        --dispatcher-url http://192.168.1.100:7429 \\
        --auth-token <shared-token> \\
        --worker-id wsl2-ryzen-7700x \\
        --max-concurrent 1 \\
        --poll-interval 5 \\
        --backends claude,gemini,grok

Respects CENTRAL_ROOT env var for portable paths (defaults to
~/projects/CENTRAL). All git worktrees go under $HOME/projects/worktrees/
which must be on the native Linux filesystem (NOT /mnt/c on WSL2).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CENTRAL_ROOT = Path(os.environ.get("CENTRAL_ROOT", str(Path.home() / "projects" / "CENTRAL")))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL = 30  # seconds between heartbeats
LOG_TAIL_LINES = 200

# Keys safe to load from shell profile — mirrors dispatcher_control.py.
# Explicitly excludes ANTHROPIC_API_KEY / OPENAI_API_KEY (see eco-insights §15).
_SAFE_SHELL_KEYS: frozenset[str] = frozenset(
    {
        "GROK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)

log = logging.getLogger("worker_agent")

# ---------------------------------------------------------------------------
# Shell API key loading (mirrors dispatcher_control.py pattern)
# ---------------------------------------------------------------------------


def _load_shell_api_keys() -> None:
    """Source allowlisted API keys from shell profile into os.environ.

    Keys like GROK_API_KEY that live only in ~/.zprofile won't be present
    when the worker agent is started from a non-login shell. We source the
    profile once at startup and merge allowlisted keys we find.
    """
    shell = "zsh"
    for profile in ("~/.zprofile", "~/.zshrc", "~/.bash_profile", "~/.bashrc"):
        path = Path(profile).expanduser()
        if not path.exists():
            continue
        try:
            result = subprocess.run(
                [shell, "-c", f"source {path} 2>/dev/null && env"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key in _SAFE_SHELL_KEYS and key not in os.environ and value:
                    os.environ[key] = value
        except Exception:
            continue


# ---------------------------------------------------------------------------
# CENTRAL path setup for backend imports
# ---------------------------------------------------------------------------


def _ensure_central_on_path() -> None:
    """Add CENTRAL scripts dir to sys.path so central_runtime_v2 is importable."""
    scripts_dir = str(CENTRAL_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


# ---------------------------------------------------------------------------
# WorkerAgent
# ---------------------------------------------------------------------------


class WorkerAgent:
    """Polls the dispatcher for work and executes tasks locally."""

    def __init__(
        self,
        dispatcher_url: str,
        auth_token: str,
        worker_id: str,
        max_concurrent: int,
        poll_interval: float,
        backends: list[str],
    ) -> None:
        self.dispatcher_url = dispatcher_url.rstrip("/")
        self.auth_token = auth_token
        self.worker_id = worker_id
        self.max_concurrent = max_concurrent
        self.poll_interval = poll_interval
        self.backends = backends

        self._central_version = "unknown"
        self._shutdown = threading.Event()
        self._active_lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}  # run_id → task state

        # State directories (under CENTRAL_ROOT for easy access via SSH/scp)
        self._log_dir = CENTRAL_ROOT / "state" / "remote_worker" / "logs"
        self._result_dir = CENTRAL_ROOT / "state" / "remote_worker" / "results"

        # Worktrees: native Linux filesystem, NOT /mnt/c on WSL2
        self._worktrees_base = Path.home() / "projects" / "worktrees"

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _sync_central(self) -> str:
        """Sync CENTRAL repo and return current short SHA.

        Logs warnings on non-zero exit codes rather than silently continuing,
        so stale-code conditions are visible in agent logs.
        """
        fetch = subprocess.run(
            ["git", "-C", str(CENTRAL_ROOT), "fetch", "--prune"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if fetch.returncode != 0:
            log.warning("CENTRAL fetch failed: %s", fetch.stderr.strip())

        pull = subprocess.run(
            ["git", "-C", str(CENTRAL_ROOT), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pull.returncode != 0:
            log.warning(
                "CENTRAL pull --ff-only failed (local commits or diverged branch?): %s",
                pull.stderr.strip(),
            )

        rev = subprocess.run(
            ["git", "-C", str(CENTRAL_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = rev.stdout.strip() or "unknown"
        log.info("CENTRAL version: %s", sha)
        return sha

    def _handle_signal(self, signum: int, frame: Any) -> None:
        log.info("Received signal %d, initiating graceful shutdown…", signum)
        self._shutdown.set()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _try_claim(self) -> dict[str, Any] | None:
        """POST /api/v1/claim. Returns work_package dict or None (no work / error)."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.dispatcher_url}/api/v1/claim",
                    headers=self._headers(),
                    json={
                        "worker_id": self.worker_id,
                        "backends": self.backends,
                        "central_version": self._central_version,
                    },
                )
            if resp.status_code == 204:
                return None
            if resp.status_code == 200:
                data = resp.json()
                if data.get("version_warning"):
                    log.warning(
                        "Dispatcher version warning: %s (consider syncing CENTRAL)",
                        data["version_warning"],
                    )
                return data.get("work_package")
            log.warning("Unexpected claim response: %d %s", resp.status_code, resp.text[:200])
            return None
        except Exception as exc:
            log.debug("Claim request failed (will retry): %s", exc)
            return None

    def _send_heartbeat(
        self,
        work: dict[str, Any],
        reattach: bool = False,
        progress: str = "running",
    ) -> int:
        """POST /api/v1/heartbeat. Returns HTTP status code (0 on network error)."""
        payload: dict[str, Any] = {
            "task_id": work["task_id"],
            "run_id": work["run_id"],
            "worker_id": self.worker_id,
            "status": "running",
            "progress_note": progress,
        }
        if reattach:
            payload["reattach"] = True
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.dispatcher_url}/api/v1/heartbeat",
                    headers=self._headers(),
                    json=payload,
                )
            return resp.status_code
        except Exception as exc:
            log.debug("Heartbeat failed (will retry next interval): %s", exc)
            return 0  # treat as transient; keep running

    def _submit_result(
        self,
        work: dict[str, Any],
        result: dict[str, Any],
        result_branch: str,
        result_commit_sha: str,
        log_tail: str,
    ) -> bool:
        """POST /api/v1/result. Returns True on success."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{self.dispatcher_url}/api/v1/result",
                    headers=self._headers(),
                    json={
                        "task_id": work["task_id"],
                        "run_id": work["run_id"],
                        "worker_id": self.worker_id,
                        "result": result,
                        "result_branch": result_branch,
                        "result_commit_sha": result_commit_sha,
                        "log_tail": log_tail,
                    },
                )
            if resp.status_code == 200:
                log.info("Result submitted for %s/%s", work["task_id"], work["run_id"])
                return True
            if resp.status_code == 409:
                log.info(
                    "Result already finalized for %s/%s (409, ignoring duplicate)",
                    work["task_id"],
                    work["run_id"],
                )
                return True
            log.error(
                "Result submission failed: %d %s", resp.status_code, resp.text[:200]
            )
            return False
        except Exception as exc:
            log.error("Result submission error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _resolve_repo_path(self, repo_root_relative: str) -> Path:
        """Resolve $HOME/{repo_root_relative} (e.g. 'projects/eco-system')."""
        return Path.home() / repo_root_relative

    def _prepare_worktree(self, repo_path: Path, run_id: str) -> Path:
        """Fetch repo, prune stale worktrees, create isolated worktree for run_id.

        Uses run_id (not task_id) so retried tasks get a fresh path even if
        the previous run's worktree was never cleaned up (crash recovery).
        Worktree is placed under $HOME/projects/worktrees/ — the native Linux
        filesystem — NOT /mnt/c (WSL2 9P bridge is 10-50x slower for git).
        """
        r = subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--prune"],
            capture_output=True,
            timeout=60,
        )
        if r.returncode != 0:
            log.warning(
                "git fetch failed for %s: %s",
                repo_path,
                r.stderr.decode(errors="replace").strip(),
            )

        # Prune stale worktrees left by prior crashes
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            capture_output=True,
            timeout=10,
        )

        worktree_path = self._worktrees_base / run_id
        if worktree_path.exists():
            log.warning("Worktree %s already exists — removing before recreating", worktree_path)
            if worktree_path.is_dir():
                shutil.rmtree(worktree_path, ignore_errors=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_path),
                    "worktree",
                    "remove",
                    "--force",
                    str(worktree_path),
                ],
                capture_output=True,
                timeout=30,
            )

        head_commit = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head_commit.returncode != 0 or not head_commit.stdout.strip():
            raise RuntimeError(
                f"Unable to resolve source commit in {repo_path}: "
                f"{(head_commit.stderr or '').strip()}"
            )
        source_ref = head_commit.stdout.strip()

        r = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                source_ref,
            ],
            capture_output=True,
            timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"git worktree add failed for {repo_path}/{run_id}: "
                f"{r.stderr.decode(errors='replace').strip()}"
            )

        return worktree_path

    def _cleanup_worktree(self, repo_path: Path, worktree_path: Path) -> None:
        """Remove worktree after task completion or cancellation."""
        r = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "worktree",
                "remove",
                "--force",
                str(worktree_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if r.returncode != 0:
            log.warning(
                "Worktree removal failed for %s: %s",
                worktree_path,
                r.stderr.decode(errors="replace").strip(),
            )
        else:
            log.debug("Worktree cleaned up: %s", worktree_path)

    def _get_branch_info(self, worktree_path: Path) -> tuple[str, str]:
        """Return (branch_name, commit_sha) from the worktree after task execution."""
        branch_r = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        branch = branch_r.stdout.strip() or "main"

        sha_r = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = sha_r.stdout.strip() or ""

        return branch, sha

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def _collect_log_tail(self, log_path: Path) -> str:
        """Return the last LOG_TAIL_LINES lines from the worker log file."""
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-LOG_TAIL_LINES:])
        except Exception:
            return ""

    def _read_result(
        self, result_path: Path, task_id: str, run_id: str, returncode: int
    ) -> dict[str, Any]:
        """Read result JSON from result_path; return a fallback FAILED result on any error."""
        try:
            if result_path.exists():
                return json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Failed to read result file %s: %s", result_path, exc)

        return {
            "schema_version": 2,
            "task_id": task_id,
            "run_id": run_id,
            "status": "FAILED" if returncode != 0 else "COMPLETED",
            "summary": (
                f"Worker subprocess exited with code {returncode} but wrote no result file"
            ),
            "completed_items": [],
            "remaining_items": [],
            "decisions": [],
            "discoveries": [],
            "blockers": ["result.json not written by worker subprocess"],
            "validation": [
                {
                    "name": "result-file",
                    "passed": False,
                    "notes": "result_path did not exist after subprocess exit",
                }
            ],
            "verdict": "rework_required",
            "requirements_assessment": [],
            "system_fit_assessment": {},
            "files_changed": [],
            "warnings": [],
            "artifacts": [],
        }

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def _run_task(self, work: dict[str, Any]) -> None:
        """Top-level task runner — runs in its own thread per task."""
        run_id = work["run_id"]
        task_id = work["task_id"]
        log.info(
            "Starting task %s (run=%s backend=%s model=%s)",
            task_id,
            run_id,
            work.get("worker_backend"),
            work.get("worker_model"),
        )

        with self._active_lock:
            self._active[run_id] = {"work": work, "cancelled": False, "proc": None}

        try:
            self._execute_task(work)
        except Exception:
            log.exception("Task %s/%s raised an unhandled exception", task_id, run_id)
        finally:
            with self._active_lock:
                self._active.pop(run_id, None)
            log.info("Task %s/%s finished, slot released", task_id, run_id)

    def _execute_task(self, work: dict[str, Any]) -> None:
        """Prepare worktree, run subprocess with heartbeat, submit result."""
        run_id = work["run_id"]
        task_id = work["task_id"]

        repo_path = self._resolve_repo_path(work["repo_root_relative"])
        worktree_path = self._prepare_worktree(repo_path, run_id)

        log_path = self._log_dir / f"{run_id}.log"
        result_path = self._result_dir / run_id / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._run_subprocess(work, repo_path, worktree_path, log_path, result_path)
        finally:
            self._cleanup_worktree(repo_path, worktree_path)

    def _run_subprocess(
        self,
        work: dict[str, Any],
        repo_path: Path,
        worktree_path: Path,
        log_path: Path,
        result_path: Path,
    ) -> None:
        """Spawn backend subprocess, heartbeat loop, collect and submit result."""
        run_id = work["run_id"]
        task_id = work["task_id"]

        # Late-binding import keeps worker_agent importable without the
        # Dispatcher repo present (useful for tests and smoke runs).
        _ensure_central_on_path()
        from central_runtime_v2.backends import get_worker_backend  # type: ignore

        backend = get_worker_backend(work.get("worker_backend", "stub"))

        # Build worker_task from work package — backends expect these fields.
        worker_task: dict[str, Any] = dict(work)
        worker_task.setdefault("id", task_id)
        worker_task["task_id"] = task_id
        worker_task["run_id"] = run_id

        snapshot: dict[str, Any] = {}  # dependencies not shipped in work package (Phase 1)
        prompt_text, command, stdin_mode = backend.prepare(
            snapshot, worker_task, run_id, result_path
        )

        env = {**os.environ, **backend.env_overrides()}

        log.info("Spawning subprocess for %s: %s", task_id, command[0])
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                command,
                cwd=str(worktree_path),
                stdin=subprocess.PIPE,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )

        # Feed prompt via stdin, then close to signal EOF.
        if prompt_text and stdin_mode == subprocess.PIPE:
            try:
                proc.stdin.write(prompt_text.encode("utf-8"))
            except Exception as exc:
                log.warning("Failed to write prompt to stdin: %s", exc)
        try:
            proc.stdin.close()
        except Exception:
            pass

        with self._active_lock:
            if run_id in self._active:
                self._active[run_id]["proc"] = proc

        # -------------------------------------------------------------------
        # Heartbeat + monitoring loop
        # -------------------------------------------------------------------
        last_heartbeat: float = 0.0
        reattach_next = False

        while proc.poll() is None:
            if self._shutdown.is_set():
                log.info("Shutdown: terminating subprocess for %s", task_id)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return

            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                status_code = self._send_heartbeat(work, reattach=reattach_next)
                last_heartbeat = now
                reattach_next = False

                if status_code == 410:
                    # Task cancelled by operator — kill immediately and clean up.
                    log.warning(
                        "Task %s cancelled by dispatcher (410), killing subprocess", task_id
                    )
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    with self._active_lock:
                        if run_id in self._active:
                            self._active[run_id]["cancelled"] = True
                    return

                elif status_code == 404:
                    # Dispatcher restarted and lost our lease — reattach on next heartbeat.
                    log.info(
                        "Task %s not found on dispatcher (404), will reattach next heartbeat",
                        task_id,
                    )
                    reattach_next = True

            time.sleep(1)

        returncode = proc.returncode
        log.info("Subprocess for %s/%s exited with code %d", task_id, run_id, returncode)

        # Skip result submission if this run was cancelled mid-flight.
        with self._active_lock:
            if self._active.get(run_id, {}).get("cancelled"):
                return

        result = self._read_result(result_path, task_id, run_id, returncode)
        result_branch, result_commit_sha = self._get_branch_info(worktree_path)
        log_tail = self._collect_log_tail(log_path)

        self._submit_result(work, result, result_branch, result_commit_sha, log_tail)

    # ------------------------------------------------------------------
    # Main poll loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Entry point: setup, then poll dispatcher until shutdown."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._result_dir.mkdir(parents=True, exist_ok=True)
        self._worktrees_base.mkdir(parents=True, exist_ok=True)

        log.info("Loading API keys from shell profile…")
        _load_shell_api_keys()

        log.info("Syncing CENTRAL at %s…", CENTRAL_ROOT)
        self._central_version = self._sync_central()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        log.info(
            "Worker agent started — id=%s backends=%s max_concurrent=%d poll_interval=%ss",
            self.worker_id,
            self.backends,
            self.max_concurrent,
            self.poll_interval,
        )

        while not self._shutdown.is_set():
            with self._active_lock:
                active_count = len(self._active)

            if active_count < self.max_concurrent:
                work = self._try_claim()
                if work is not None:
                    t = threading.Thread(
                        target=self._run_task,
                        args=(work,),
                        daemon=True,
                        name=f"task-{work['run_id']}",
                    )
                    t.start()
                    continue  # immediately try to fill more slots (if max_concurrent > 1)

            self._shutdown.wait(timeout=self.poll_interval)

        # Give running tasks a short grace window to finish cleanly.
        log.info("Shutdown: waiting for active tasks to complete (max 60s)…")
        deadline = time.time() + 60.0
        while self._active and time.time() < deadline:
            time.sleep(1)

        if self._active:
            log.warning(
                "Shutdown: %d task(s) still active after grace window", len(self._active)
            )

        log.info("Worker agent stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="CENTRAL remote worker agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dispatcher-url", required=True, help="Dispatcher coordination API base URL"
    )
    parser.add_argument(
        "--auth-token", required=True, help="Bearer token (must match CENTRAL_COORDINATION_TOKEN)"
    )
    parser.add_argument("--worker-id", required=True, help="Unique worker identifier")
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        help="Maximum concurrent tasks (Phase 1 default: 1)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between claim attempts when idle",
    )
    parser.add_argument(
        "--backends",
        default="claude,gemini,grok",
        help="Comma-separated list of backends this worker can execute",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    if not backends:
        parser.error("--backends must include at least one backend")

    agent = WorkerAgent(
        dispatcher_url=args.dispatcher_url,
        auth_token=args.auth_token,
        worker_id=args.worker_id,
        max_concurrent=args.max_concurrent,
        poll_interval=args.poll_interval,
        backends=backends,
    )
    agent.run()


if __name__ == "__main__":
    main()
