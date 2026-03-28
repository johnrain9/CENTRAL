#!/usr/bin/env python3
"""Behavior tests for central_runtime decision helpers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime


class CentralRuntimeBehaviorTest(unittest.TestCase):
    def test_build_claude_command_appends_extra_args(self) -> None:
        command = central_runtime.build_claude_command(
            {"task_id": "CENTRAL-OPS-171", "run_id": "run-171"},
            Path("/tmp/result.json"),
            model="claude-sonnet-4-6",
            extra_args=["--resume", "sess-123", "--fork-session"],
        )

        script = command[2]
        self.assertIn("--resume", script)
        self.assertIn("sess-123", script)
        self.assertIn("--fork-session", script)

    def test_build_claude_command_omits_session_fork_flags_when_extra_args_none(self) -> None:
        command = central_runtime.build_claude_command(
            {"task_id": "CENTRAL-OPS-171", "run_id": "run-171"},
            Path("/tmp/result.json"),
            model="claude-sonnet-4-6",
            extra_args=None,
        )

        script = command[2]
        self.assertNotIn("--resume", script)
        self.assertNotIn("--fork-session", script)

    def test_claude_backend_prepare_uses_session_fork_args_and_logs(self) -> None:
        backend = central_runtime.ClaudeBackend()
        snapshot = {"task_id": "CENTRAL-OPS-171", "target_repo_id": "TEST", "dependencies": []}
        worker_task = {
            "task_id": "CENTRAL-OPS-171",
            "worker_model": "claude-sonnet-4-6",
            "db_path": "/tmp/test.db",
        }
        fork_result = central_runtime.session_manager.SessionForkResult(
            args=["--resume", "sess-123", "--fork-session"],
            session_id="sess-123",
            stale=False,
            stale_reason=None,
        )

        with (
            mock.patch.object(central_runtime, "_build_worker_prompt", return_value="prompt body") as prompt_mock,
            mock.patch.object(central_runtime.session_manager, "get_fork_args", return_value=fork_result) as get_fork_args_mock,
            mock.patch.object(backend, "_log_session_fork") as log_session_fork_mock,
        ):
            prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, "run-171", Path("/tmp/result.json"))

        self.assertEqual(prompt_text, "prompt body")
        self.assertEqual(stdin_mode, central_runtime.subprocess.PIPE)
        prompt_mock.assert_called_once_with(snapshot, worker_task, "run-171")
        get_fork_args_mock.assert_called_once_with("TEST", Path("/tmp/test.db"))
        log_session_fork_mock.assert_called_once_with("CENTRAL-OPS-171", "TEST", Path("/tmp/test.db"), fork_result)
        self.assertIn("--resume", command[2])
        self.assertIn("sess-123", command[2])

    def test_claude_backend_prepare_cold_starts_when_no_session_fork_exists(self) -> None:
        backend = central_runtime.ClaudeBackend()
        snapshot = {"task_id": "CENTRAL-OPS-171", "target_repo_id": "TEST", "dependencies": []}
        worker_task = {
            "task_id": "CENTRAL-OPS-171",
            "worker_model": "claude-sonnet-4-6",
            "db_path": "/tmp/test.db",
        }

        with (
            mock.patch.object(central_runtime, "_build_worker_prompt", return_value="prompt body") as prompt_mock,
            mock.patch.object(central_runtime.session_manager, "get_fork_args", return_value=None) as get_fork_args_mock,
            mock.patch.object(backend, "_log_session_fork") as log_session_fork_mock,
        ):
            prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, "run-171", Path("/tmp/result.json"))

        self.assertEqual(prompt_text, "prompt body")
        self.assertEqual(stdin_mode, central_runtime.subprocess.PIPE)
        prompt_mock.assert_called_once_with(snapshot, worker_task, "run-171")
        get_fork_args_mock.assert_called_once_with("TEST", Path("/tmp/test.db"))
        log_session_fork_mock.assert_not_called()
        self.assertNotIn("--resume", command[2])
        self.assertNotIn("--fork-session", command[2])

    def test_claude_backend_log_session_fork_emits_stale_events(self) -> None:
        backend = central_runtime.ClaudeBackend()
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = {"fork_count": 12}
        result = central_runtime.session_manager.SessionForkResult(
            args=["--resume", "sess-123", "--fork-session"],
            session_id="sess-123",
            stale=True,
            stale_reason="fork_count_exceeded(50)",
        )

        with (
            mock.patch.object(central_runtime.task_db, "connect", return_value=conn) as connect_mock,
            mock.patch.object(central_runtime.task_db, "insert_event") as insert_event_mock,
        ):
            backend._log_session_fork("CENTRAL-OPS-171", "TEST", Path("/tmp/test.db"), result)

        connect_mock.assert_called_once_with(Path("/tmp/test.db"))
        conn.execute.assert_called_once_with(
            "SELECT fork_count FROM session_registry WHERE session_id = ?",
            ("sess-123",),
        )
        self.assertEqual(insert_event_mock.call_count, 2)
        self.assertEqual(insert_event_mock.call_args_list[0].kwargs["event_type"], "session.forked")
        self.assertEqual(insert_event_mock.call_args_list[0].kwargs["payload"]["fork_count"], 12)
        self.assertEqual(insert_event_mock.call_args_list[1].kwargs["event_type"], "session.stale_detected")
        self.assertEqual(
            insert_event_mock.call_args_list[1].kwargs["payload"]["reason"],
            "fork_count_exceeded(50)",
        )
        conn.commit.assert_called_once_with()

    def test_resolve_task_class_uses_override_then_metadata_signals(self) -> None:
        self.assertEqual(
            central_runtime.resolve_task_class({"execution": {"metadata": {"task_class": "Design"}}}),
            "design",
        )
        self.assertEqual(
            central_runtime.resolve_task_class({"metadata": {"tags": ["architecture"]}}),
            "design",
        )
        self.assertEqual(
            central_runtime.resolve_task_class({"metadata": {"phase": "detailed planning"}}),
            "design",
        )
        self.assertEqual(central_runtime.resolve_task_class({"metadata": {}}), "routine")

    def test_resolve_policy_model_switches_by_task_class_and_backend(self) -> None:
        high, source = central_runtime.resolve_policy_model("design", "codex")
        self.assertEqual(source, "policy_default")
        self.assertEqual(high, central_runtime.HIGH_TIER_CODEX_MODEL)

        medium, _ = central_runtime.resolve_policy_model("routine", "claude")
        self.assertEqual(medium, central_runtime.MEDIUM_TIER_CLAUDE_MODEL)

    def test_build_worker_task_uses_per_task_backend_and_model_override(self) -> None:
        snapshot = {
            "task_id": "CENTRAL-OPS-2200",
            "title": "Backend selection",
            "objective_md": "Do work",
            "context_md": "ctx",
            "scope_md": "- scope a\n- scope b",
            "deliverables_md": "- out a\n- out b",
            "acceptance_md": "- ok",
            "testing_md": "- pytest -q",
            "dispatch_md": "dispatch",
            "closeout_md": "close",
            "reconciliation_md": "reconcile",
            "task_type": "implementation",
            "target_repo_root": str(REPO_ROOT),
            "metadata": {},
            "execution": {
                "task_kind": "mutating",
                "sandbox_mode": "workspace-write",
                "approval_policy": "never",
                "additional_writable_dirs": [],
                "metadata": {
                    "worker_backend": "claude",
                    "claude_model": "claude-3-7-sonnet",
                },
            },
        }

        task = central_runtime.build_worker_task(snapshot, "gpt-5.4", worker_mode="codex", dispatcher_default_worker_model="claude-default")
        self.assertEqual(task["worker_backend"], "claude")
        self.assertEqual(task["worker_model"], "claude-3-7-sonnet")
        self.assertEqual(task["worker_model_source"], "task_override")
        self.assertEqual(json.loads(task["deliverables_json"]), ["out a", "out b"])
        self.assertEqual(json.loads(task["scope_notes_json"]), ["scope a", "scope b"])

    def test_classify_worker_run_distinguishes_stuck_healthy_and_recent_issue(self) -> None:
        active_snapshot = {
            "runtime": {"runtime_status": "running"},
            "lease": {
                "lease_acquired_at": "2026-03-20T10:00:00+00:00",
                "lease_expires_at": "2026-03-20T10:01:00+00:00",
            },
        }
        stuck_state, stuck_reason = central_runtime.classify_worker_run(
            active_snapshot,
            heartbeat_age=120.0,
            seconds_to_lease_expiry=-1.0,
            log_info={"age_seconds": 120.0},
            log_growth={"bytes_since_last_inspection": 0},
            runtime_event_age=120.0,
            transition_age=120.0,
        )
        self.assertEqual(stuck_state, "potentially_stuck")
        self.assertIn("lease expired", stuck_reason)

        healthy_state, _ = central_runtime.classify_worker_run(
            active_snapshot,
            heartbeat_age=2.0,
            seconds_to_lease_expiry=20.0,
            log_info={"age_seconds": 2.0},
            log_growth={"bytes_since_last_inspection": 8},
            runtime_event_age=3.0,
            transition_age=3.0,
        )
        self.assertEqual(healthy_state, "healthy")

        issue_state, _ = central_runtime.classify_worker_run(
            {"runtime": {"runtime_status": "failed"}, "lease": {}},
            heartbeat_age=None,
            seconds_to_lease_expiry=None,
            log_info={"age_seconds": None},
            log_growth={"bytes_since_last_inspection": None},
            runtime_event_age=None,
            transition_age=None,
        )
        self.assertEqual(issue_state, "recent_issue")

    def test_summarize_validation_results_includes_name_status_and_notes(self) -> None:
        summary = central_runtime.summarize_validation_results(
            [
                {"name": "lint", "passed": True, "notes": "clean"},
                {"name": "tests", "passed": False, "notes": "2 failing"},
            ]
        )
        self.assertEqual(summary, "lint: passed (clean); tests: failed (2 failing)")


if __name__ == "__main__":
    unittest.main()
