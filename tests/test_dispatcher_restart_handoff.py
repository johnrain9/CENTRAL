#!/usr/bin/env python3
"""Restart handoff smoke test for the CENTRAL dispatcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_CONTROL = REPO_ROOT / "scripts" / "dispatcher_control.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


TASK_ID = "CENTRAL-RESTART-SMOKE"
WORKER_SLEEP_SECONDS = 14.0


def worker_task_payload() -> dict[str, object]:
    return {
        "task_id": TASK_ID,
        "title": "Dispatcher restart handoff smoke task",
        "summary": "Validate dispatcher restart-safe worker adoption",
        "objective_md": "Keep a long-running stub worker alive across dispatcher restart.",
        "context_md": "Synthetic runtime smoke task for CENTRAL-OPS-31.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- preserve supervision across dispatcher restart",
        "acceptance_md": "- worker keeps running and finalizes successfully after adoption",
        "testing_md": "- automated restart smoke only",
        "dispatch_md": "Dispatch locally through dispatcher_control.py with stub mode.",
        "closeout_md": "Inspect runtime state and events only.",
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
            "metadata": {
                "stub_sleep_seconds": WORKER_SLEEP_SECONDS,
                "stub_log_interval_seconds": 0.5,
            },
        },
        "dependencies": [],
    }


class DispatcherRestartHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_dispatcher_restart_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        self.state_dir = tmp_path / "runtime_state"
        self.env = os.environ.copy()
        self.env["CENTRAL_TASK_DB_PATH"] = str(self.db_path)
        self.env["CENTRAL_RUNTIME_STATE_DIR"] = str(self.state_dir)
        self.env["CENTRAL_WORKER_MODE"] = "stub"

        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.create_task(conn, worker_task_payload(), actor_kind="self_check", actor_id="central.runtime")
        finally:
            conn.close()

    def tearDown(self) -> None:
        try:
            self.run_dispatcher("stop", check=False, timeout=20)
        finally:
            worker_pid = self.worker_pid_from_snapshot()
            if worker_pid is not None:
                try:
                    os.kill(worker_pid, 15)
                except OSError:
                    pass
            self.tmpdir.cleanup()

    def run_dispatcher(self, *args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DISPATCHER_CONTROL), *args],
            cwd=str(REPO_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            self.fail(f"dispatcher {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def fetch_snapshot(self) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=TASK_ID)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def fetch_events(self) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (TASK_ID,),
            ).fetchall()
            return [str(row["event_type"]) for row in rows]
        finally:
            conn.close()

    def wait_for(self, predicate, *, timeout: float, interval: float = 0.2):
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except AssertionError as exc:
                last_error = exc
            time.sleep(interval)
        if last_error is not None:
            raise last_error
        self.fail("timed out waiting for condition")

    def worker_pid_from_snapshot(self) -> int | None:
        snapshot = self.fetch_snapshot()
        metadata = ((snapshot.get("lease") or {}).get("metadata") or {})
        supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
        if not isinstance(supervision, dict):
            return None
        try:
            return int(supervision.get("worker_pid"))
        except (TypeError, ValueError):
            return None

    def dispatcher_pid(self) -> int | None:
        lock_path = self.state_dir / "dispatcher.lock"
        if not lock_path.exists():
            return None
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid"))
        except Exception:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def test_restart_adopts_running_worker(self) -> None:
        self.run_dispatcher("start", "--max-workers", "1")

        snapshot = self.wait_for(
            lambda: (
                current
                if isinstance((((current := self.fetch_snapshot()).get("lease") or {}).get("metadata") or {}).get("supervision"), dict)
                else None
            ),
            timeout=10.0,
        )
        initial_dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)
        initial_heartbeat = (snapshot.get("lease") or {}).get("last_heartbeat_at")
        worker_pid = self.worker_pid_from_snapshot()
        self.assertIsNotNone(worker_pid)
        os.kill(worker_pid, 0)

        restart_started = time.time()
        self.run_dispatcher("restart", "--max-workers", "1", timeout=20.0)
        restart_elapsed = time.time() - restart_started
        self.assertLess(restart_elapsed, WORKER_SLEEP_SECONDS - 4.0)
        os.kill(worker_pid, 0)

        adopted_dispatcher_pid = self.wait_for(
            lambda: pid if (pid := self.dispatcher_pid()) and pid != initial_dispatcher_pid else None,
            timeout=10.0,
        )
        self.assertNotEqual(initial_dispatcher_pid, adopted_dispatcher_pid)

        adopted_snapshot = self.wait_for(
            lambda: self.fetch_snapshot() if "runtime.worker_adopted" in self.fetch_events() else None,
            timeout=10.0,
        )
        adopted_heartbeat = (adopted_snapshot.get("lease") or {}).get("last_heartbeat_at")
        self.assertNotEqual(initial_heartbeat, adopted_heartbeat)

        finished_snapshot = self.wait_for(
            lambda: (
                current
                if str(((current := self.fetch_snapshot()).get("runtime") or {}).get("runtime_status") or "") == "done"
                else None
            ),
            timeout=WORKER_SLEEP_SECONDS + 15.0,
        )
        self.assertIsNone(finished_snapshot.get("lease"))
        self.assertIn("runtime.dispatcher_handoff_requested", self.fetch_events())
        self.assertIn("runtime.worker_adopted", self.fetch_events())


if __name__ == "__main__":
    unittest.main()
