#!/usr/bin/env python3
"""Operator kill-task coverage for the CENTRAL dispatcher."""

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


ACTIVE_TASK_ID = "CENTRAL-OPS-3101"
INACTIVE_TASK_ID = "CENTRAL-OPS-3102"
WORKER_SLEEP_SECONDS = 20.0


def worker_task_payload(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "initiative": "one-off",
        "title": f"Kill task coverage for {task_id}",
        "summary": "Validate operator kill-task behavior",
        "objective_md": "Verify operator stop intent prevents immediate retry.",
        "context_md": "Synthetic dispatcher kill-task coverage.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- operator kill-task marks the task failed",
        "acceptance_md": "- killed tasks do not immediately retry",
        "testing_md": "- automated dispatcher kill-task tests only",
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
        "metadata": {"test_task": True, "audit_required": False},
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


class DispatcherKillTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_dispatcher_kill_")
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
                task_db.ensure_repo(
                    conn,
                    repo_id="CENTRAL",
                    repo_root=str(REPO_ROOT),
                    display_name="CENTRAL",
                )
                task_db.create_task(conn, worker_task_payload(ACTIVE_TASK_ID), actor_kind="test", actor_id="dispatcher.kill")
                task_db.create_task(conn, worker_task_payload(INACTIVE_TASK_ID), actor_kind="test", actor_id="dispatcher.kill")
            # Initialize runtime state for INACTIVE_TASK_ID (no active worker, just queued state)
            claim = task_db.runtime_claim(
                conn,
                worker_id="setUp-worker",
                queue_name="default",
                lease_seconds=5,
                task_id=INACTIVE_TASK_ID,
                actor_id="dispatcher.kill.setup",
            )
            if claim is not None:
                task_db.runtime_requeue_task(
                    conn,
                    task_id=INACTIVE_TASK_ID,
                    actor_id="dispatcher.kill.setup",
                    reason="setUp: initialize runtime state without active lease",
                )
        finally:
            conn.close()

    def tearDown(self) -> None:
        try:
            self.run_dispatcher("stop", check=False, timeout=20.0)
        finally:
            self.cleanup_workers()
            self.tmpdir.cleanup()

    def cleanup_workers(self) -> None:
        for task_id in (ACTIVE_TASK_ID, INACTIVE_TASK_ID):
            pid = self.worker_pid_from_snapshot(task_id)
            if pid is None:
                continue
            try:
                os.kill(pid, 15)
            except OSError:
                pass

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

    def fetch_snapshot(self, task_id: str) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def fetch_events(self, task_id: str) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (task_id,),
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

    def worker_pid_from_snapshot(self, task_id: str) -> int | None:
        snapshot = self.fetch_snapshot(task_id)
        metadata = ((snapshot.get("lease") or {}).get("metadata") or {})
        supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
        if not isinstance(supervision, dict):
            return None
        try:
            return int(supervision.get("worker_pid"))
        except (TypeError, ValueError):
            return None

    def test_kill_task_stops_active_worker_and_blocks_retry(self) -> None:
        self.run_dispatcher("start", "--max-workers", "1")
        active_snapshot = self.wait_for(
            lambda: (
                current
                if str(((current := self.fetch_snapshot(ACTIVE_TASK_ID)).get("runtime") or {}).get("runtime_status") or "") == "running"
                else None
            ),
            timeout=10.0,
        )
        worker_pid = self.worker_pid_from_snapshot(ACTIVE_TASK_ID)
        self.assertIsNotNone(worker_pid)
        os.kill(worker_pid, 0)

        result = self.run_dispatcher(
            "kill-task",
            ACTIVE_TASK_ID,
            "--reason",
            "operator terminated stuck worker",
            "--json",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_id"], ACTIVE_TASK_ID)
        self.assertEqual(payload["snapshot"]["planner_status"], "failed")
        self.assertEqual(payload["snapshot"]["runtime"]["runtime_status"], "failed")
        self.assertTrue(payload["kill_target"]["had_active_lease"])

        self.wait_for(
            lambda: self.fetch_snapshot(ACTIVE_TASK_ID) if self.fetch_snapshot(ACTIVE_TASK_ID).get("lease") is None else None,
            timeout=10.0,
        )
        self.wait_for(
            lambda: True if not self._pid_alive(worker_pid) else None,
            timeout=10.0,
        )
        self.assertIn("runtime.operator_stop_requested", self.fetch_events(ACTIVE_TASK_ID))
        self.assertIn("planner.operator_stop_reconciled", self.fetch_events(ACTIVE_TASK_ID))

        time.sleep(2.5)
        after_kill = self.fetch_snapshot(ACTIVE_TASK_ID)
        self.assertEqual(after_kill["planner_status"], "failed")
        self.assertEqual((after_kill.get("runtime") or {}).get("runtime_status"), "failed")
        self.assertIsNone(after_kill.get("lease"))
        self.assertEqual(self.worker_pid_from_snapshot(ACTIVE_TASK_ID), None)
        self.assertEqual(active_snapshot["task_id"], ACTIVE_TASK_ID)

    def test_kill_task_fails_inactive_task_without_worker(self) -> None:
        result = self.run_dispatcher(
            "kill-task",
            INACTIVE_TASK_ID,
            "--reason",
            "operator canceled queued work",
            "--json",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_id"], INACTIVE_TASK_ID)
        self.assertFalse(payload["kill_target"]["had_active_lease"])
        self.assertFalse(payload["worker_termination"]["worker_present"])

        snapshot = self.fetch_snapshot(INACTIVE_TASK_ID)
        self.assertEqual(snapshot["planner_status"], "failed")
        self.assertEqual((snapshot.get("runtime") or {}).get("runtime_status"), "failed")
        self.assertIsNone(snapshot.get("lease"))

        claim = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "central_task_db.py"),
                "runtime-claim",
                "--task-id",
                INACTIVE_TASK_ID,
                "--worker-id",
                "test-worker",
            ],
            cwd=str(REPO_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(claim.returncode, 0)
        self.assertIn("no eligible task available to claim", claim.stderr or claim.stdout)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


if __name__ == "__main__":
    unittest.main()
