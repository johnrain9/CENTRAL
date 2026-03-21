#!/usr/bin/env python3
"""Tests for explicit dispatcher Codex model selection."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime
import central_task_db as task_db
import dispatcher_control


def task_payload(task_id: str, *, codex_model: str | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {
        "stub_sleep_seconds": 0.1,
        "stub_log_interval_seconds": 0.05,
    }
    if codex_model is not None:
        metadata["codex_model"] = codex_model
    return {
        "task_id": task_id,
        "initiative": "one-off",
        "title": f"{task_id} codex model test",
        "summary": "Exercise dispatcher Codex model selection.",
        "objective_md": "Capture the worker command that CENTRAL would spawn.",
        "context_md": "Temporary DB only.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- record the selected --model flag",
        "acceptance_md": "- dispatcher passes an explicit Codex model to the worker",
        "testing_md": "- automated unittest coverage only",
        "dispatch_md": "Dispatch locally through the CENTRAL runtime harness.",
        "closeout_md": "Synthetic runtime closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": {"test_case": task_id},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": metadata,
        },
        "dependencies": [],
    }


class FakePipe:
    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.closed = False

    def write(self, text: str) -> None:
        self.chunks.append(text)

    def close(self) -> None:
        self.closed = True


class FakePopen:
    instances: list["FakePopen"] = []
    next_pid = 50000

    def __init__(self, command, **kwargs) -> None:
        self.command = list(command)
        self.kwargs = kwargs
        self.pid = FakePopen.next_pid
        FakePopen.next_pid += 1
        self.stdin = FakePipe() if kwargs.get("stdin") == subprocess.PIPE else None
        FakePopen.instances.append(self)

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None


class DispatcherControlCodexModelTest(unittest.TestCase):
    def test_saved_codex_model_is_persisted_and_used(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_codex_config_") as tmpdir:
            state_dir = Path(tmpdir)
            config_path = state_dir / "dispatcher-config.json"
            with mock.patch.object(dispatcher_control, "STATE_DIR", state_dir), mock.patch.object(
                dispatcher_control, "CONFIG_PATH", config_path
            ), mock.patch.dict(os.environ, {}, clear=False):
                dispatcher_control.save_config(max_workers=2, codex_model="saved-model")
                self.assertEqual(dispatcher_control.saved_codex_model(), "saved-model")
                resolved = dispatcher_control.resolve_codex_model(None, restart=False)
                self.assertEqual(resolved.value, "saved-model")
                self.assertEqual(resolved.source, "saved_config")

    def test_codex_model_resolution_precedence(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.CODEX_MODEL_ENV: "env-model"}, clear=False):
            resolved = dispatcher_control.resolve_codex_model("cli-model", restart=False)
            self.assertEqual((resolved.value, resolved.source), ("cli-model", "cli"))

            resolved = dispatcher_control.resolve_codex_model(None, restart=False)
            self.assertEqual((resolved.value, resolved.source), ("env-model", "model_env"))

        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
            dispatcher_control,
            "running_lock_payload",
            return_value={"default_codex_model": "running-model"},
        ):
            resolved = dispatcher_control.resolve_codex_model(None, restart=True)
            self.assertEqual((resolved.value, resolved.source), ("running-model", "running_daemon"))


class CentralRuntimeCodexModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_runtime_codex_model_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        self.state_dir = tmp_path / "runtime_state"
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
        finally:
            conn.close()
        FakePopen.instances.clear()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def create_task(self, payload: dict[str, object]) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(conn, payload, actor_kind="test", actor_id="dispatcher.codex.tests")
        finally:
            conn.close()

    def fetch_snapshot(self, task_id: str) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def claim_snapshot(self, task_id: str) -> dict[str, object]:
        dispatcher = central_runtime.CentralDispatcher(
            central_runtime.DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="codex",
                default_codex_model="dispatcher-default-model",
            )
        )
        snapshot = dispatcher._claim_next()
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["task_id"], task_id)
        return snapshot

    def spawn_and_capture(self, snapshot: dict[str, object], *, default_codex_model: str) -> tuple[FakePopen, dict[str, object]]:
        dispatcher = central_runtime.CentralDispatcher(
            central_runtime.DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="codex",
                default_codex_model=default_codex_model,
            )
        )
        with mock.patch.object(central_runtime.subprocess, "Popen", FakePopen), mock.patch.object(
            central_runtime,
            "process_start_token",
            return_value=None,
        ):
            dispatcher._spawn_worker(snapshot)
        self.assertEqual(len(FakePopen.instances), 1)
        spawned = FakePopen.instances[0]
        task_id = str(snapshot["task_id"])
        state = dispatcher._active[task_id]
        dispatcher._close_worker_state(state)
        dispatcher._active.clear()
        return spawned, self.fetch_snapshot(task_id)

    def assert_model_flag(self, command: list[str], expected_model: str) -> None:
        self.assertIn("--model", command)
        index = command.index("--model")
        self.assertEqual(command[index + 1], expected_model)

    def test_dispatcher_default_model_is_passed_to_worker(self) -> None:
        task_id = "CENTRAL-OPS-9480"
        self.create_task(task_payload(task_id))
        snapshot = self.claim_snapshot(task_id)
        spawned, latest_snapshot = self.spawn_and_capture(snapshot, default_codex_model="dispatcher-default-model")

        self.assert_model_flag(spawned.command, "dispatcher-default-model")
        runtime_notes = str(((latest_snapshot.get("runtime") or {}).get("metadata") or {}).get("notes") or "")
        self.assertIn("model=dispatcher-default-model", runtime_notes)
        self.assertIn("model_source=dispatcher_default", runtime_notes)
        supervision = (((latest_snapshot.get("lease") or {}).get("metadata") or {}).get("supervision") or {})
        self.assertEqual(supervision.get("worker_model"), "dispatcher-default-model")
        self.assertEqual(supervision.get("worker_model_source"), "dispatcher_default")
        # Backward compat: codex fields still written for codex backend
        self.assertEqual(supervision.get("codex_model"), "dispatcher-default-model")
        self.assertEqual(supervision.get("codex_model_source"), "dispatcher_default")

    def test_task_specific_model_override_wins(self) -> None:
        task_id = "CENTRAL-OPS-9481"
        self.create_task(task_payload(task_id, codex_model="task-override-model"))
        snapshot = self.claim_snapshot(task_id)
        spawned, latest_snapshot = self.spawn_and_capture(snapshot, default_codex_model="dispatcher-default-model")

        self.assert_model_flag(spawned.command, "task-override-model")
        runtime_notes = str(((latest_snapshot.get("runtime") or {}).get("metadata") or {}).get("notes") or "")
        self.assertIn("model=task-override-model", runtime_notes)
        self.assertIn("model_source=task_override", runtime_notes)
        supervision = (((latest_snapshot.get("lease") or {}).get("metadata") or {}).get("supervision") or {})
        self.assertEqual(supervision.get("worker_model"), "task-override-model")
        self.assertEqual(supervision.get("worker_model_source"), "task_override")
        # Backward compat: codex fields still written for codex backend
        self.assertEqual(supervision.get("codex_model"), "task-override-model")
        self.assertEqual(supervision.get("codex_model_source"), "task_override")


if __name__ == "__main__":
    unittest.main()
