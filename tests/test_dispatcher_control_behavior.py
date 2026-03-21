#!/usr/bin/env python3
"""Behavior tests for dispatcher_control precedence and kill-task handling."""

from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dispatcher_control


class DispatcherControlBehaviorTest(unittest.TestCase):
    def test_resolve_max_workers_precedence(self) -> None:
        with mock.patch.object(dispatcher_control, "env_max_workers", return_value=9), mock.patch.object(
            dispatcher_control, "running_lock_payload", return_value={"max_workers": 7}
        ), mock.patch.object(dispatcher_control, "saved_max_workers", return_value=5):
            self.assertEqual(dispatcher_control.resolve_max_workers(3, restart=True).value, 3)
            self.assertEqual(dispatcher_control.resolve_max_workers(None, restart=False).value, 9)
            self.assertEqual(dispatcher_control.resolve_max_workers(None, restart=True).value, 9)

        with mock.patch.object(dispatcher_control, "env_max_workers", return_value=None), mock.patch.object(
            dispatcher_control, "running_lock_payload", return_value={"max_workers": 7}
        ), mock.patch.object(dispatcher_control, "saved_max_workers", return_value=5):
            resolved = dispatcher_control.resolve_max_workers(None, restart=True)
            self.assertEqual((resolved.value, resolved.source), (7, "running_daemon"))

    def test_resolve_worker_model_uses_generic_env_before_codex_env(self) -> None:
        with mock.patch.object(dispatcher_control, "env_worker_model", return_value="generic-model"), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value="codex-model"
        ), mock.patch.object(dispatcher_control, "running_lock_payload", return_value={}), mock.patch.object(
            dispatcher_control, "saved_worker_model", return_value=None
        ):
            resolved = dispatcher_control.resolve_worker_model(None, restart=True)
            self.assertEqual((resolved.value, resolved.source), ("generic-model", "model_env"))

    def test_resolve_effort_invalid_value_fails_fast(self) -> None:
        with mock.patch.dict("os.environ", {dispatcher_control.CODEX_EFFORT_ENV: "ultra"}, clear=False):
            with self.assertRaises(SystemExit):
                dispatcher_control.resolve_effort()

    def test_terminate_worker_reports_success_when_process_exits(self) -> None:
        kill_target = {"worker_pid": 123, "worker_process_start_token": "tok"}
        with mock.patch.object(dispatcher_control, "process_matches", side_effect=[True, False]), mock.patch(
            "dispatcher_control.os.kill"
        ):
            result = dispatcher_control.terminate_worker(kill_target)
        self.assertEqual(result["terminated"], True)
        self.assertEqual(result["pid"], 123)

    def test_kill_task_adds_worker_termination_and_prints_human_summary(self) -> None:
        payload = {
            "reason": "operator requested stop",
            "kill_target": {"worker_pid": 222, "worker_process_start_token": "tok"},
            "snapshot": {"planner_status": "in_progress", "runtime": {"runtime_status": "running"}},
        }
        cp = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(dispatcher_control, "ensure_runtime"), mock.patch.object(dispatcher_control, "init_db"), mock.patch(
            "dispatcher_control.subprocess.run", return_value=cp
        ), mock.patch.object(
            dispatcher_control, "terminate_worker", return_value={"worker_present": True, "terminated": True, "pid": 222}
        ):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = dispatcher_control.kill_task(task_id="CENTRAL-OPS-1", reason="why", as_json=False)
        self.assertEqual(rc, 0)
        rendered = out.getvalue()
        self.assertIn("Task CENTRAL-OPS-1 failed by operator kill-task", rendered)
        self.assertIn("Worker: terminated (pid 222)", rendered)
        self.assertIn("Reason: operator requested stop", rendered)


if __name__ == "__main__":
    unittest.main()
