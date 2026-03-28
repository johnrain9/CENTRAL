#!/usr/bin/env python3
"""Behavior tests for central_runtime_v2.backends Claude session forking."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db
import session_manager
from central_runtime_v2 import backends


class CentralRuntimeV2BackendsBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp_path = Path(self.tmpdir.name)

    def _init_db_with_repo(self) -> Path:
        db_path = self.tmp_path / "central_tasks.db"
        conn = task_db.connect(db_path)
        self.addCleanup(conn.close)
        migrations = task_db.load_migrations(task_db.DEFAULT_MIGRATIONS_DIR)
        task_db.apply_migrations(conn, migrations)
        repo_root = self.tmp_path / "repo"
        repo_root.mkdir(exist_ok=True)
        task_db.ensure_repo(
            conn,
            repo_id="TEST",
            repo_root=str(repo_root),
            display_name="Test Repo",
            metadata={"session_persistence_enabled": True},
        )
        conn.commit()
        return db_path

    def _insert_active_session(self, conn, repo_root: Path, session_id: str) -> None:
        conn.execute(
            """
            INSERT INTO session_registry (
                repo_id, session_id, session_name, status, seed_started_at, seed_completed_at,
                fork_count, context_tokens, seed_model, seed_cwd, seed_prompt_hash, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TEST",
                session_id,
                f"{session_id}-name",
                "active",
                "2026-03-27T00:00:00+00:00",
                "2026-03-27T00:00:00+00:00",
                0,
                None,
                "claude-sonnet-4-6",
                str(repo_root),
                None,
                "test",
                "2026-03-27T00:00:00+00:00",
                "2026-03-27T00:00:00+00:00",
            ),
        )
        conn.commit()

    def _write_session_file(self, repo_root: Path, session_id: str) -> Path:
        projects_dir = self.tmp_path / ".claude" / "projects"
        project_dir = projects_dir / session_manager._claude_project_dir_name(repo_root)
        project_dir.mkdir(parents=True, exist_ok=True)
        session_file = project_dir / f"{session_id}.jsonl"
        session_file.write_text('{"type":"result"}\n', encoding="utf-8")
        return projects_dir

    def test_build_claude_command_includes_session_fork_flags_when_extra_args_present(self) -> None:
        command = backends.build_claude_command(
            {"task_id": "CENTRAL-OPS-172", "run_id": "run-172"},
            Path("/tmp/result.json"),
            model="claude-sonnet-4-6",
            extra_args=["--resume", "sess-v2", "--fork-session"],
        )

        script = command[2]
        self.assertIn("--resume", script)
        self.assertIn("sess-v2", script)
        self.assertIn("--fork-session", script)

    def test_build_claude_command_omits_session_fork_flags_when_extra_args_none(self) -> None:
        command = backends.build_claude_command(
            {"task_id": "CENTRAL-OPS-172", "run_id": "run-172"},
            Path("/tmp/result.json"),
            model="claude-sonnet-4-6",
            extra_args=None,
        )

        script = command[2]
        self.assertNotIn("--resume", script)
        self.assertNotIn("--fork-session", script)

    def test_claude_backend_prepare_uses_get_fork_args_and_threads_flags_into_command(self) -> None:
        backend = backends.ClaudeBackend()
        snapshot = {"task_id": "CENTRAL-OPS-172", "target_repo_id": "TEST", "dependencies": []}
        worker_task = {
            "task_id": "CENTRAL-OPS-172",
            "worker_model": "claude-sonnet-4-6",
            "db_path": "/tmp/test.db",
            "prompt_body": "prompt",
        }
        fork_result = session_manager.SessionForkResult(
            args=["--resume", "sess-threaded", "--fork-session"],
            session_id="sess-threaded",
            stale=False,
            stale_reason=None,
        )

        with (
            mock.patch.object(session_manager, "get_fork_args", return_value=fork_result) as get_fork_args_mock,
            mock.patch.object(backend, "_log_session_fork") as log_session_fork_mock,
        ):
            prompt_text, command, stdin_mode = backend.prepare(
                snapshot,
                worker_task,
                "run-172",
                Path("/tmp/result.json"),
            )

        self.assertEqual(prompt_text, "prompt")
        self.assertEqual(stdin_mode, backends.subprocess.PIPE)
        get_fork_args_mock.assert_called_once_with("TEST", Path("/tmp/test.db"))
        log_session_fork_mock.assert_called_once_with(
            "CENTRAL-OPS-172",
            "TEST",
            Path("/tmp/test.db"),
            fork_result,
        )
        self.assertIn("--resume", command[2])
        self.assertIn("sess-threaded", command[2])
        self.assertIn("--fork-session", command[2])

    def test_claude_backend_prepare_end_to_end_uses_active_session_registry_for_fork_flags(self) -> None:
        db_path = self._init_db_with_repo()
        conn = task_db.connect(db_path)
        self.addCleanup(conn.close)
        repo_root = self.tmp_path / "repo"
        self._insert_active_session(conn, repo_root, "sess-active")
        projects_dir = self._write_session_file(repo_root, "sess-active")

        backend = backends.ClaudeBackend()
        snapshot = {"task_id": "CENTRAL-OPS-172", "target_repo_id": "TEST", "dependencies": []}
        worker_task = {
            "task_id": "CENTRAL-OPS-172",
            "worker_model": "claude-sonnet-4-6",
            "db_path": str(db_path),
            "prompt_body": "prompt",
        }

        with (
            mock.patch.object(session_manager, "CLAUDE_PROJECTS_DIR", projects_dir),
            mock.patch.object(backend, "_log_session_fork"),
        ):
            _, command, _ = backend.prepare(snapshot, worker_task, "run-172", Path("/tmp/result.json"))

        script = command[2]
        self.assertIn("--resume", script)
        self.assertIn("sess-active", script)
        self.assertIn("--fork-session", script)
        row = conn.execute(
            "SELECT fork_count, last_forked_at FROM session_registry WHERE session_id = ?",
            ("sess-active",),
        ).fetchone()
        self.assertEqual(row["fork_count"], 1)
        self.assertIsNotNone(row["last_forked_at"])


if __name__ == "__main__":
    unittest.main()
