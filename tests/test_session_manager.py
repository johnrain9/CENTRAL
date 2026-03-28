#!/usr/bin/env python3
"""Tests for the standalone session manager module."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db
import session_manager


class SessionManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"
        self.repo_root = self.tmp_path / "repo"
        self.repo_root.mkdir()
        self.conn = task_db.connect(self.db_path)
        migrations = task_db.load_migrations(task_db.DEFAULT_MIGRATIONS_DIR)
        task_db.apply_migrations(self.conn, migrations)
        task_db.ensure_repo(
            self.conn,
            repo_id="TEST",
            repo_root=str(self.repo_root),
            display_name="Test Repo",
            metadata={"session_persistence_enabled": True},
        )
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.projects_dir = self.tmp_path / ".claude" / "projects"

    def _insert_session(
        self,
        *,
        session_id: str,
        status: str = "active",
        fork_count: int = 0,
        seed_completed_at: str = "2026-03-27T00:00:00+00:00",
        seed_prompt_hash: str | None = None,
        context_tokens: int | None = None,
    ) -> None:
        self.conn.execute(
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
                status,
                "2026-03-27T00:00:00+00:00",
                seed_completed_at,
                fork_count,
                context_tokens,
                "claude-sonnet-4-6",
                str(self.repo_root),
                seed_prompt_hash,
                "test",
                "2026-03-27T00:00:00+00:00",
                "2026-03-27T00:00:00+00:00",
            ),
        )
        self.conn.commit()

    def _write_session_file(self, session_id: str) -> Path:
        project_dir = self.projects_dir / session_manager._claude_project_dir_name(self.repo_root)
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{session_id}.jsonl"
        path.write_text('{"type":"result"}\n', encoding="utf-8")
        return path

    def test_get_fork_args_returns_none_before_session_registry_migration(self) -> None:
        conn = task_db.connect(self.tmp_path / "pre_migration.db")
        self.addCleanup(conn.close)
        first_migration = task_db.load_migrations(task_db.DEFAULT_MIGRATIONS_DIR)[0]
        task_db.apply_migrations(conn, [first_migration])
        task_db.ensure_repo(
            conn,
            repo_id="TEST",
            repo_root=str(self.repo_root),
            display_name="Test Repo",
            metadata={"session_persistence_enabled": True},
        )
        conn.commit()

        self.assertIsNone(session_manager.get_fork_args("TEST", self.tmp_path / "pre_migration.db"))

    def test_get_fork_args_returns_none_after_session_registry_rollback(self) -> None:
        self.conn.execute("DROP TABLE session_registry")
        self.conn.commit()

        self.assertIsNone(session_manager.get_fork_args("TEST", self.db_path))

    def test_get_fork_args_returns_none_when_feature_gate_disabled(self) -> None:
        self.conn.execute(
            "UPDATE repos SET metadata_json = ? WHERE repo_id = ?",
            (json.dumps({"session_persistence_enabled": False}), "TEST"),
        )
        self.conn.commit()
        self._insert_session(session_id="sess-disabled")
        self._write_session_file("sess-disabled")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            self.assertIsNone(session_manager.get_fork_args("TEST", self.db_path))

    def test_validate_session_matches_exact_repo_project_dir(self) -> None:
        self._write_session_file("sess-1")
        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            self.assertTrue(session_manager.validate_session("sess-1", repo_root=self.repo_root))
            self.assertFalse(session_manager.validate_session("missing", repo_root=self.repo_root))

    def test_validate_session_returns_false_for_missing_files_with_mocked_filesystem(self) -> None:
        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir), patch.object(
            Path, "glob", return_value=[]
        ), patch.object(Path, "is_file", return_value=False):
            self.assertFalse(session_manager.validate_session("missing", repo_root=self.repo_root))

    def test_validate_session_falls_back_to_global_scan_when_repo_dir_misses(self) -> None:
        other_repo_root = self.tmp_path / "other-repo"
        other_repo_root.mkdir()
        project_dir = self.projects_dir / session_manager._claude_project_dir_name(other_repo_root)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "sess-fallback.jsonl").write_text('{"type":"result"}\n', encoding="utf-8")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            self.assertTrue(session_manager.validate_session("sess-fallback", repo_root=self.repo_root))

    def test_get_fork_args_demotes_invalid_session_to_retired(self) -> None:
        self._insert_session(session_id="sess-invalid")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            self.assertIsNone(session_manager.get_fork_args("TEST", self.db_path))

        row = self.conn.execute(
            "SELECT status, notes FROM session_registry WHERE session_id = ?",
            ("sess-invalid",),
        ).fetchone()
        self.assertEqual(row["status"], "retired")
        self.assertIn("validation_failed", str(row["notes"]))

    def test_get_fork_args_falls_back_to_stale_after_invalid_active(self) -> None:
        self._insert_session(session_id="bad-active", status="active")
        self._insert_session(
            session_id="good-stale",
            status="stale",
            seed_completed_at="2026-03-27T08:00:00+00:00",
        )
        self._write_session_file("good-stale")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            result = session_manager.get_fork_args("TEST", self.db_path)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.session_id, "good-stale")
        active_row = self.conn.execute(
            "SELECT status FROM session_registry WHERE session_id = ?",
            ("bad-active",),
        ).fetchone()
        self.assertEqual(active_row["status"], "retired")

    def test_get_fork_args_returns_resume_and_fork_flags_for_valid_active_session(self) -> None:
        self._insert_session(session_id="sess-active")
        self._write_session_file("sess-active")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            result = session_manager.get_fork_args("TEST", self.db_path)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.args, ["--resume", "sess-active", "--fork-session"])
        self.assertEqual(result.session_id, "sess-active")
        self.assertFalse(result.stale)
        self.assertIsNone(result.stale_reason)
        row = self.conn.execute(
            "SELECT fork_count, last_forked_at FROM session_registry WHERE session_id = ?",
            ("sess-active",),
        ).fetchone()
        self.assertEqual(row["fork_count"], 1)
        self.assertIsNotNone(row["last_forked_at"])

    def test_get_fork_args_prefers_most_recent_stale_session(self) -> None:
        self._insert_session(
            session_id="older",
            status="stale",
            seed_completed_at="2026-03-26T00:00:00+00:00",
        )
        self._insert_session(
            session_id="newer",
            status="stale",
            seed_completed_at="2026-03-27T08:00:00+00:00",
        )
        self._write_session_file("newer")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            result = session_manager.get_fork_args("TEST", self.db_path)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.session_id, "newer")
        row = self.conn.execute(
            "SELECT fork_count FROM session_registry WHERE session_id = ?",
            ("newer",),
        ).fetchone()
        self.assertEqual(row["fork_count"], 1)

    def test_get_fork_args_marks_prompt_hash_change_as_stale(self) -> None:
        prompt_path = self.repo_root / "seed_prompt.md"
        prompt_path.write_text("current prompt", encoding="utf-8")
        self.conn.execute(
            "UPDATE repos SET metadata_json = ? WHERE repo_id = ?",
            (json.dumps({"session_persistence_enabled": True, "session_seed_prompt_file": "seed_prompt.md"}), "TEST"),
        )
        self.conn.commit()
        self._insert_session(session_id="sess-stale", seed_prompt_hash="outdated")
        self._write_session_file("sess-stale")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            result = session_manager.get_fork_args("TEST", self.db_path)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.stale)
        self.assertEqual(result.stale_reason, "prompt_hash_changed")

    def test_get_fork_args_treats_missing_prompt_file_as_stale_not_fatal(self) -> None:
        self.conn.execute(
            "UPDATE repos SET metadata_json = ? WHERE repo_id = ?",
            (json.dumps({"session_persistence_enabled": True, "session_seed_prompt_file": "missing.md"}), "TEST"),
        )
        self.conn.commit()
        self._insert_session(session_id="sess-stale", seed_prompt_hash="outdated")
        self._write_session_file("sess-stale")

        with patch.object(session_manager, "CLAUDE_PROJECTS_DIR", self.projects_dir):
            result = session_manager.get_fork_args("TEST", self.db_path)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.stale)
        self.assertEqual(result.stale_reason, "prompt_hash_unavailable")

    def test_is_stale_checks_fork_count_age_and_prompt_hash(self) -> None:
        repo_row, meta = session_manager._load_repo(self.conn, "TEST")  # type: ignore[misc]
        assert repo_row is not None

        count_row = {
            "fork_count": 50,
            "seed_completed_at": "2026-03-27T00:00:00+00:00",
            "seed_prompt_hash": None,
            "context_tokens": None,
        }
        self.assertTrue(session_manager._is_stale(count_row, meta, repo_row))

        with patch.object(session_manager, "_utc_now", return_value=session_manager.datetime(2026, 3, 31, tzinfo=session_manager.timezone.utc)):
            age_row = {
                "fork_count": 0,
                "seed_completed_at": "2026-03-27T00:00:00+00:00",
                "seed_prompt_hash": None,
                "context_tokens": None,
            }
            self.assertTrue(session_manager._is_stale(age_row, meta, repo_row))

        prompt_path = self.repo_root / "seed_prompt.md"
        prompt_path.write_text("current prompt", encoding="utf-8")
        self.conn.execute(
            "UPDATE repos SET metadata_json = ? WHERE repo_id = ?",
            (json.dumps({"session_persistence_enabled": True, "session_seed_prompt_file": "seed_prompt.md"}), "TEST"),
        )
        self.conn.commit()
        repo_row, meta = session_manager._load_repo(self.conn, "TEST")  # type: ignore[misc]
        prompt_row = {
            "fork_count": 0,
            "seed_completed_at": "2026-03-27T00:00:00+00:00",
            "seed_prompt_hash": "old",
            "context_tokens": None,
        }
        self.assertTrue(session_manager._is_stale(prompt_row, meta, repo_row))

    def test_seed_session_uses_repo_root_cwd_and_retires_previous_active(self) -> None:
        self._insert_session(session_id="old-active", status="active")
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout='{"usage":{"input_tokens":12,"output_tokens":3}}\n',
            stderr="",
        )

        with patch.object(session_manager, "uuid4", return_value="new-session"), patch.object(
            session_manager.subprocess, "run", return_value=completed
        ) as run_mock:
            session_id = session_manager.seed_session("TEST", self.db_path, model="claude-opus")

        self.assertEqual(session_id, "new-session")
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(self.repo_root))
        self.assertIn("--session-id", run_mock.call_args.args[0])
        rows = self.conn.execute(
            "SELECT session_id, status, context_tokens, seed_cwd FROM session_registry ORDER BY session_id"
        ).fetchall()
        self.assertEqual([(row["session_id"], row["status"]) for row in rows], [("new-session", "active"), ("old-active", "retired")])
        self.assertEqual(rows[0]["context_tokens"], 15)
        self.assertEqual(rows[0]["seed_cwd"], str(self.repo_root))

    def test_seed_session_deletes_failed_seed_row(self) -> None:
        completed = subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="boom")

        with patch.object(session_manager, "uuid4", return_value="bad-session"), patch.object(
            session_manager.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                session_manager.seed_session("TEST", self.db_path)

        count = self.conn.execute("SELECT COUNT(*) AS count FROM session_registry WHERE session_id = ?", ("bad-session",)).fetchone()
        self.assertEqual(count["count"], 0)

    def test_refresh_session_retires_old_active_and_stale_rows(self) -> None:
        self._insert_session(session_id="active-1", status="active")
        self._insert_session(session_id="stale-1", status="stale")
        self._insert_session(session_id="stale-2", status="stale", seed_completed_at="2026-03-26T00:00:00+00:00")
        completed = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")

        with patch.object(session_manager, "uuid4", return_value="fresh"), patch.object(
            session_manager.subprocess, "run", return_value=completed
        ):
            session_id = session_manager.refresh_session("TEST", self.db_path)

        self.assertEqual(session_id, "fresh")
        rows = self.conn.execute(
            "SELECT session_id, status FROM session_registry ORDER BY session_id"
        ).fetchall()
        self.assertEqual(
            [(row["session_id"], row["status"]) for row in rows],
            [("active-1", "retired"), ("fresh", "active"), ("stale-1", "retired"), ("stale-2", "retired")],
        )
        stale_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM session_registry WHERE repo_id = ? AND status = 'stale'",
            ("TEST",),
        ).fetchone()
        self.assertEqual(stale_count["count"], 0)

    def test_refresh_session_preserves_existing_fallbacks_when_seed_fails(self) -> None:
        self._insert_session(session_id="active-1", status="active")
        self._insert_session(session_id="stale-1", status="stale")
        completed = subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="seed failed")

        with patch.object(session_manager, "uuid4", return_value="fresh"), patch.object(
            session_manager.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(RuntimeError, "seed failed"):
                session_manager.refresh_session("TEST", self.db_path)

        rows = self.conn.execute(
            "SELECT session_id, status FROM session_registry ORDER BY session_id"
        ).fetchall()
        self.assertEqual(
            [(row["session_id"], row["status"]) for row in rows],
            [("active-1", "stale"), ("stale-1", "stale")],
        )
        count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM session_registry WHERE session_id = ?",
            ("fresh",),
        ).fetchone()
        self.assertEqual(count["count"], 0)

    def test_list_sessions_filters_by_repo(self) -> None:
        self._insert_session(session_id="one")
        rows = session_manager.list_sessions(self.db_path, repo_id="TEST")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repo_id"], "TEST")

    def test_session_registry_enforces_single_active_row_per_repo(self) -> None:
        self._insert_session(session_id="active-1", status="active")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_session(session_id="active-2", status="active")


if __name__ == "__main__":
    unittest.main()
