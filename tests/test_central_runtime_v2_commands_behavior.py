#!/usr/bin/env python3
"""Behavior tests for central_runtime_v2.commands."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from central_runtime_v2 import commands


class FakeRowResult:
    def __init__(self, rows: list[dict[str, object]] | None = None, row: dict[str, object] | None = None) -> None:
        self._rows = rows or []
        self._row = row or {}

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object]:
        return self._row


class FakeConn:
    def __init__(self, rows: list[dict[str, object]] | None = None, active_leases: int = 0) -> None:
        self.rows = rows or []
        self.active_leases = active_leases
        self.closed = False

    def execute(self, query: str) -> FakeRowResult:
        if "GROUP BY runtime_status" in query:
            return FakeRowResult(rows=self.rows)
        if "FROM task_active_leases" in query:
            return FakeRowResult(row={"c": self.active_leases})
        raise AssertionError(f"unexpected query: {query}")

    def close(self) -> None:
        self.closed = True


class CommandsBehaviorTest(unittest.TestCase):
    def test_status_payload_reports_running_dispatcher_configuration(self) -> None:
        fake_conn = FakeConn(
            rows=[{"runtime_status": "running", "c": 2}, {"runtime_status": "done", "c": 1}],
            active_leases=3,
        )
        paths = commands.build_runtime_paths(Path("/tmp/central-runtime-status"))
        with mock.patch("central_runtime_v2.commands.read_lock", return_value={
            "pid": "222",
            "max_workers": "4",
            "worker_mode": "codex",
            "default_worker_model": "gpt-5.4",
            "default_codex_model": "gpt-5.3-codex",
        }), mock.patch("central_runtime_v2.commands.pid_alive", return_value=True), mock.patch(
            "central_runtime_v2.commands.connect_initialized", return_value=fake_conn
        ), mock.patch(
            "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[{"task_id": "A"}]
        ), mock.patch(
            "central_runtime_v2.commands.task_db.order_eligible_snapshots", return_value=[{"task_id": "A"}]
        ):
            payload = commands.status_payload(Path("/tmp/central.db"), paths)

        self.assertTrue(payload["running"])
        self.assertEqual(payload["configured_max_workers"], 4)
        self.assertEqual(payload["configured_default_worker_model"], "gpt-5.4")
        self.assertEqual(payload["runtime_counts"], {"running": 2, "done": 1})
        self.assertEqual(payload["active_leases"], 3)
        self.assertTrue(fake_conn.closed)

    def test_worker_status_payload_builds_active_and_recent_worker_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            paths = commands.build_runtime_paths(state_dir)
            commands.ensure_runtime_dirs(paths)

            active_task_id = "TASK-ACTIVE"
            recent_task_id = "TASK-RECENT"
            active_log = paths.worker_logs_dir / active_task_id / "run-active.log"
            active_prompt = paths.worker_prompts_dir / active_task_id / "run-active.md"
            active_result = paths.worker_results_dir / active_task_id / "run-active.json"
            active_log.parent.mkdir(parents=True, exist_ok=True)
            active_prompt.parent.mkdir(parents=True, exist_ok=True)
            active_result.parent.mkdir(parents=True, exist_ok=True)
            active_log.write_text("worker boot\nheartbeat ok\n", encoding="utf-8")
            active_prompt.write_text("# Prompt\n", encoding="utf-8")
            active_result.write_text('{"status":"done"}\n', encoding="utf-8")
            paths.worker_status_cache_path.write_text(
                json.dumps(
                    {
                        "workers": {
                            f"{active_task_id}:run-active:{active_log}": {
                                "observed_at": "2026-03-20T09:59:00+00:00",
                                "size_bytes": 5,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            recent_artifact_dir = state_dir / "artifacts"
            recent_artifact_dir.mkdir(parents=True, exist_ok=True)
            recent_log = recent_artifact_dir / "run-recent.log"
            recent_result = recent_artifact_dir / "run-recent.json"
            recent_prompt = recent_artifact_dir / "run-recent.md"
            recent_log.write_text("finished\n", encoding="utf-8")
            recent_result.write_text('{"status":"COMPLETED"}\n', encoding="utf-8")
            recent_prompt.write_text("# Done\n", encoding="utf-8")

            snapshots = [
                {
                    "task_id": active_task_id,
                    "title": "Active worker",
                    "lease": {
                        "lease_owner_id": "worker-1",
                        "execution_run_id": "run-active",
                        "last_heartbeat_at": "2026-03-20T10:00:55+00:00",
                        "lease_expires_at": "2026-03-20T10:01:30+00:00",
                        "metadata": {
                            "supervision": {
                                "worker_backend": "codex",
                                "worker_model": "gpt-5.4",
                                "worker_model_source": "policy_default",
                            }
                        },
                    },
                    "runtime": {
                        "runtime_status": "running",
                        "claimed_by": "worker-1",
                        "claimed_at": "2026-03-20T10:00:00+00:00",
                        "started_at": "2026-03-20T10:00:01+00:00",
                        "last_transition_at": "2026-03-20T10:00:01+00:00",
                        "retry_count": 0,
                        "last_runtime_error": None,
                    },
                },
                {
                    "task_id": recent_task_id,
                    "title": "Recent worker",
                    "lease": None,
                    "runtime": {
                        "runtime_status": "done",
                        "finished_at": "2026-03-20T10:00:20+00:00",
                        "last_transition_at": "2026-03-20T10:00:20+00:00",
                        "retry_count": 1,
                        "last_runtime_error": None,
                    },
                },
            ]
            events = {
                active_task_id: [
                    {"event_type": "runtime.started", "created_at": "2026-03-20T10:00:01+00:00"},
                    {"event_type": "runtime.heartbeat", "created_at": "2026-03-20T10:00:55+00:00"},
                ],
                recent_task_id: [
                    {"event_type": "runtime.completed", "created_at": "2026-03-20T10:00:20+00:00"},
                ],
            }
            artifacts = {
                active_task_id: [],
                recent_task_id: [
                    {"artifact_kind": "prompt", "path_or_uri": str(recent_prompt), "created_at": "2026-03-20T10:00:20+00:00"},
                    {"artifact_kind": "log", "path_or_uri": str(recent_log), "created_at": "2026-03-20T10:00:20+00:00"},
                    {"artifact_kind": "result", "path_or_uri": str(recent_result), "created_at": "2026-03-20T10:00:20+00:00"},
                ],
            }

            with mock.patch("central_runtime_v2.commands.status_payload", return_value={"running": True}), mock.patch(
                "central_runtime_v2.commands.connect_initialized", return_value=mock.MagicMock(close=lambda: None)
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=snapshots
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_latest_events",
                side_effect=lambda _conn, task_id, limit=20: events[task_id],
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_artifacts",
                side_effect=lambda _conn, task_id: artifacts[task_id],
            ), mock.patch(
                "central_runtime_v2.commands.datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 3, 20, 10, 1, 0, tzinfo=timezone.utc)
                fake_datetime.fromtimestamp.side_effect = lambda *args, **kwargs: datetime.fromtimestamp(*args, **kwargs)
                fake_datetime.fromisoformat.side_effect = lambda *args, **kwargs: datetime.fromisoformat(*args, **kwargs)
                payload = commands.worker_status_payload(
                    Path("/tmp/central.db"),
                    paths,
                    task_id=None,
                    recent_limit=5,
                    recent_hours=24.0,
                )
                saved_cache = json.loads(paths.worker_status_cache_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["overall_status"], "healthy")
        self.assertEqual(payload["summary"]["active_count"], 1)
        self.assertEqual(payload["summary"]["recent_count"], 1)
        self.assertEqual(payload["active_workers"][0]["run_id"], "run-active")
        self.assertEqual(payload["active_workers"][0]["observed_state"], "healthy")
        self.assertGreater(payload["active_workers"][0]["log"]["growth"]["bytes_since_last_inspection"], 0)
        self.assertEqual(payload["recent_workers"][0]["run_id"], "run-recent")
        self.assertEqual(payload["recent_workers"][0]["observed_state"], "recently_finished")
        self.assertIn("workers", saved_cache)

    def test_build_dispatcher_config_maps_args_and_resolves_default_model(self) -> None:
        args = argparse.Namespace(
            db_path=None,
            state_dir=None,
            max_workers=3,
            poll_interval=0.3,
            heartbeat_seconds=1.0,
            status_heartbeat_seconds=2.0,
            stale_recovery_seconds=3.0,
            worker_mode="claude",
            default_worker_model=None,
            default_codex_model="claude-fallback",
            notify=True,
        )
        with mock.patch("central_runtime_v2.commands.resolve_default_worker_model", return_value="resolved-model"):
            cfg = commands.build_dispatcher_config(args)

        self.assertEqual(cfg.max_workers, 3)
        self.assertEqual(cfg.worker_mode, "claude")
        self.assertEqual(cfg.default_worker_model, "resolved-model")
        self.assertTrue(cfg.notify)

    def test_command_status_and_worker_status_render_json_and_text(self) -> None:
        status_args = argparse.Namespace(db_path=None, state_dir=None, json=True)
        worker_args = argparse.Namespace(db_path=None, state_dir=None, task_id="TASK-1", limit=3, recent_hours=4.0, json=False)
        with mock.patch("central_runtime_v2.commands.task_db.resolve_db_path", return_value=Path("/tmp/central.db")), mock.patch(
            "central_runtime_v2.commands.resolve_state_dir", return_value=Path("/tmp/state")
        ), mock.patch(
            "central_runtime_v2.commands.build_runtime_paths", return_value=mock.sentinel.paths
        ), mock.patch(
            "central_runtime_v2.commands.ensure_runtime_dirs"
        ), mock.patch(
            "central_runtime_v2.commands.status_payload", return_value={"running": True}
        ), mock.patch(
            "central_runtime_v2.commands.worker_status_payload", return_value={"summary": {}, "active_workers": [], "recent_workers": []}
        ), mock.patch(
            "central_runtime_v2.commands.worker_status_text", return_value="worker text"
        ):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(commands.command_status(status_args), 0)
                self.assertEqual(commands.command_worker_status(worker_args), 0)

        rendered = out.getvalue()
        self.assertIn('"running": true', rendered)
        self.assertIn("worker text", rendered)

    def test_command_stop_releases_stale_lock(self) -> None:
        args = argparse.Namespace(state_dir=None, db_path=None)
        with mock.patch("central_runtime_v2.commands.read_lock", return_value={"pid": 42}), mock.patch(
            "central_runtime_v2.commands.pid_alive", return_value=False
        ), mock.patch("central_runtime_v2.commands.release_lock") as release_lock:
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_stop(args)

        self.assertEqual(rc, 0)
        self.assertIn("dispatcher_not_running", out.getvalue())
        release_lock.assert_called_once()

    def test_command_stop_sends_sigterm_for_live_dispatcher(self) -> None:
        args = argparse.Namespace(state_dir=None, db_path=None)
        with mock.patch("central_runtime_v2.commands.read_lock", return_value={"pid": 42}), mock.patch(
            "central_runtime_v2.commands.pid_alive", return_value=True
        ), mock.patch("central_runtime_v2.commands.os.kill") as kill:
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_stop(args)

        self.assertEqual(rc, 0)
        self.assertIn("stop_signal_sent pid=42", out.getvalue())
        kill.assert_called_once_with(42, commands.signal.SIGTERM)

    def test_command_tail_uses_daemon_log_tail_output(self) -> None:
        args = argparse.Namespace(state_dir=None, db_path=None, lines=5, follow=False)
        fake_log = mock.MagicMock()
        fake_log.tail.return_value = "line-a\nline-b"
        with mock.patch("central_runtime_v2.commands.resolve_state_dir", return_value=Path("/tmp/state")), mock.patch(
            "central_runtime_v2.commands.build_runtime_paths", return_value=mock.sentinel.paths
        ), mock.patch(
            "central_runtime_v2.commands.ensure_runtime_dirs"
        ), mock.patch(
            "central_runtime_v2.commands.DaemonLog", return_value=fake_log
        ), mock.patch(
            "sys.stdout.isatty", return_value=False
        ):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_tail(args)

        self.assertEqual(rc, 0)
        self.assertIn("line-a", out.getvalue())
        fake_log.tail.assert_called_once_with(lines=5, colorize=False)

    def test_command_run_once_and_daemon_delegate_to_dispatcher(self) -> None:
        args = argparse.Namespace(
            db_path=None,
            state_dir=None,
            max_workers=1,
            poll_interval=1.0,
            heartbeat_seconds=2.0,
            status_heartbeat_seconds=3.0,
            stale_recovery_seconds=4.0,
            worker_mode="stub",
            default_worker_model=None,
            default_codex_model=None,
            notify=False,
            audit_worker_model=None,
        )
        fake_dispatcher = mock.MagicMock()
        fake_dispatcher.run_once.return_value = 7
        fake_dispatcher.run_daemon.return_value = 8
        with mock.patch("central_runtime_v2.commands.build_dispatcher_config", return_value=mock.sentinel.cfg), mock.patch(
            "central_runtime_v2.commands.CentralDispatcher", return_value=fake_dispatcher
        ):
            self.assertEqual(commands.command_run_once(args), 7)
            self.assertEqual(commands.command_daemon(args), 8)

        fake_dispatcher.run_once.assert_called_once_with(emit_result=True)
        fake_dispatcher.run_daemon.assert_called_once_with()

    def test_build_parser_and_main_dispatch_expected_subcommands(self) -> None:
        parser = commands.build_parser()
        parsed = parser.parse_args(["worker-status", "--limit", "2", "--json"])
        self.assertEqual(parsed.limit, 2)
        self.assertTrue(parsed.json)

        called = {}

        def fake_func(args: argparse.Namespace) -> int:
            called["command"] = args.command
            return 11

        with mock.patch("central_runtime_v2.commands.build_parser") as build_parser:
            fake_parser = mock.MagicMock()
            fake_parser.parse_args.return_value = argparse.Namespace(command="status", func=fake_func)
            build_parser.return_value = fake_parser
            self.assertEqual(commands.main(["central-runtime", "status"]), 11)

        self.assertEqual(called["command"], "status")

    def test_smoke_task_payload_contains_required_creation_fields(self) -> None:
        payload = commands.smoke_task_payload()
        self.assertEqual(payload["initiative"], "one-off")
        self.assertIn("metadata", payload)
        self.assertIn("execution", payload)
        self.assertEqual(payload["task_id"], commands.SELF_CHECK_TASK_ID)

    def test_status_payload_handles_invalid_pid_in_lock(self) -> None:
        fake_conn = FakeConn(rows=[], active_leases=0)
        paths = commands.build_runtime_paths(Path("/tmp/central-runtime-bad-pid"))
        with mock.patch("central_runtime_v2.commands.read_lock", return_value={"pid": "not-a-number"}), mock.patch(
            "central_runtime_v2.commands.pid_alive", return_value=False
        ), mock.patch(
            "central_runtime_v2.commands.connect_initialized", return_value=fake_conn
        ), mock.patch(
            "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[]
        ), mock.patch(
            "central_runtime_v2.commands.task_db.order_eligible_snapshots", return_value=[]
        ):
            payload = commands.status_payload(Path("/tmp/central.db"), paths)

        self.assertIsNone(payload["pid"])
        self.assertFalse(payload["running"])

    def test_status_payload_worker_model_fallback_when_worker_model_none(self) -> None:
        fake_conn = FakeConn(rows=[], active_leases=0)
        paths = commands.build_runtime_paths(Path("/tmp/central-runtime-fallback"))
        with mock.patch("central_runtime_v2.commands.read_lock", return_value={
            "pid": "555",
            "max_workers": "2",
            "worker_mode": None,
            "default_worker_model": None,  # will fall back to codex model
            "default_codex_model": "codex-fallback",
        }), mock.patch("central_runtime_v2.commands.pid_alive", return_value=True), mock.patch(
            "central_runtime_v2.commands.connect_initialized", return_value=fake_conn
        ), mock.patch(
            "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[]
        ), mock.patch(
            "central_runtime_v2.commands.task_db.order_eligible_snapshots", return_value=[]
        ):
            payload = commands.status_payload(Path("/tmp/central.db"), paths)

        # When default_worker_model is None, it falls back to the codex model
        self.assertEqual(payload["configured_default_codex_model"], "codex-fallback")
        self.assertEqual(payload["configured_default_worker_model"], "codex-fallback")

    def test_command_stop_returns_not_running_when_no_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(state_dir=tmpdir, db_path=None)
            with mock.patch("central_runtime_v2.commands.read_lock", return_value=None):
                out = StringIO()
                with mock.patch("sys.stdout", out):
                    rc = commands.command_stop(args)

        self.assertEqual(rc, 0)
        self.assertIn("dispatcher_not_running", out.getvalue())

    def test_command_stop_calls_die_for_invalid_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(state_dir=tmpdir, db_path=None)
            # die() raises SystemExit — simulate that so execution stops there
            with mock.patch(
                "central_runtime_v2.commands.read_lock", return_value={"pid": None}
            ), mock.patch("central_runtime_v2.commands.die", side_effect=SystemExit(1)) as die_mock:
                with self.assertRaises(SystemExit):
                    commands.command_stop(args)

        die_mock.assert_called_once_with("invalid dispatcher lock payload")

    def test_command_status_non_json_branch_emits_json(self) -> None:
        args = argparse.Namespace(db_path=None, state_dir=None, json=False)
        with mock.patch("central_runtime_v2.commands.task_db.resolve_db_path", return_value=Path("/tmp/central.db")), mock.patch(
            "central_runtime_v2.commands.resolve_state_dir", return_value=Path("/tmp/state")
        ), mock.patch(
            "central_runtime_v2.commands.build_runtime_paths", return_value=mock.sentinel.paths
        ), mock.patch(
            "central_runtime_v2.commands.ensure_runtime_dirs"
        ), mock.patch(
            "central_runtime_v2.commands.status_payload", return_value={"running": False}
        ):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_status(args)

        self.assertEqual(rc, 0)
        self.assertIn('"running": false', out.getvalue())

    def test_command_worker_status_json_branch_emits_json(self) -> None:
        args = argparse.Namespace(db_path=None, state_dir=None, task_id=None, limit=5, recent_hours=1.0, json=True)
        fake_payload = {
            "summary": {"overall_status": "idle"},
            "active_workers": [],
            "recent_workers": [],
        }
        with mock.patch("central_runtime_v2.commands.task_db.resolve_db_path", return_value=Path("/tmp/central.db")), mock.patch(
            "central_runtime_v2.commands.resolve_state_dir", return_value=Path("/tmp/state")
        ), mock.patch(
            "central_runtime_v2.commands.build_runtime_paths", return_value=mock.sentinel.paths
        ), mock.patch(
            "central_runtime_v2.commands.worker_status_payload", return_value=fake_payload
        ):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_worker_status(args)

        self.assertEqual(rc, 0)
        self.assertIn('"overall_status": "idle"', out.getvalue())

    def test_worker_status_payload_headline_variants(self) -> None:
        """Test potentially_stuck and healthy+low_activity headline paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            paths = commands.build_runtime_paths(state_dir)
            commands.ensure_runtime_dirs(paths)

            stuck_snapshot = {
                "task_id": "TASK-STUCK",
                "title": "Stuck worker",
                "lease": {
                    "lease_owner_id": "worker-stuck",
                    "execution_run_id": "run-stuck",
                    "last_heartbeat_at": "2020-01-01T00:00:00+00:00",
                    "lease_expires_at": "2020-01-01T00:01:00+00:00",  # way in the past
                    "lease_acquired_at": "2020-01-01T00:00:00+00:00",
                    "metadata": {"supervision": {"worker_backend": "stub", "worker_model": None}},
                },
                "runtime": {
                    "runtime_status": "running",
                    "claimed_by": "worker-stuck",
                    "claimed_at": "2020-01-01T00:00:00+00:00",
                    "started_at": "2020-01-01T00:00:01+00:00",
                    "last_transition_at": "2020-01-01T00:00:01+00:00",
                    "retry_count": 0,
                    "last_runtime_error": None,
                },
            }

            with mock.patch("central_runtime_v2.commands.status_payload", return_value={"running": True}), mock.patch(
                "central_runtime_v2.commands.connect_initialized", return_value=mock.MagicMock(close=lambda: None)
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[stuck_snapshot]
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_latest_events", return_value=[]
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_artifacts", return_value=[]
            ):
                payload = commands.worker_status_payload(
                    Path("/tmp/central.db"),
                    paths,
                    task_id=None,
                    recent_limit=5,
                    recent_hours=24.0,
                )

        self.assertEqual(payload["summary"]["overall_status"], "potentially_stuck")
        self.assertIn("stuck", payload["summary"]["headline"])

    def test_worker_status_payload_idle_and_low_activity_headlines(self) -> None:
        """Test idle headline (no active workers) and low_activity headline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            paths = commands.build_runtime_paths(state_dir)
            commands.ensure_runtime_dirs(paths)

            # Idle: no snapshots at all
            with mock.patch("central_runtime_v2.commands.status_payload", return_value={"running": False}), mock.patch(
                "central_runtime_v2.commands.connect_initialized", return_value=mock.MagicMock(close=lambda: None)
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[]
            ):
                idle_payload = commands.worker_status_payload(
                    Path("/tmp/central.db"), paths, task_id=None, recent_limit=5, recent_hours=24.0
                )

            self.assertEqual(idle_payload["summary"]["overall_status"], "idle")
            self.assertIn("No active", idle_payload["summary"]["headline"])

            # Low-activity: one running worker with stale-ish heartbeat and log
            # Lease window ~70min → recent_window=120s, stale_window=600s
            # Heartbeat age 180s: > recent_window but < stale_window → low_activity
            low_snapshot = {
                "task_id": "TASK-LOW",
                "title": "Quiet worker",
                "lease": {
                    "lease_owner_id": "worker-low",
                    "execution_run_id": "run-low",
                    "last_heartbeat_at": "2026-03-20T09:58:00+00:00",  # 3min ago
                    "lease_acquired_at": "2026-03-20T09:50:00+00:00",  # 11min ago
                    "lease_expires_at": "2026-03-20T11:00:00+00:00",   # 59min from now
                    "metadata": {"supervision": {}},
                },
                "runtime": {
                    "runtime_status": "running",
                    "claimed_by": "worker-low",
                    "claimed_at": "2026-03-20T09:50:00+00:00",
                    "started_at": "2026-03-20T09:50:01+00:00",
                    "last_transition_at": "2026-03-20T09:50:01+00:00",
                    "retry_count": 0,
                    "last_runtime_error": None,
                },
            }

            with mock.patch("central_runtime_v2.commands.status_payload", return_value={"running": True}), mock.patch(
                "central_runtime_v2.commands.connect_initialized", return_value=mock.MagicMock(close=lambda: None)
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_task_snapshots", return_value=[low_snapshot]
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_latest_events", return_value=[]
            ), mock.patch(
                "central_runtime_v2.commands.task_db.fetch_artifacts", return_value=[]
            ), mock.patch(
                "central_runtime_v2.commands.datetime"
            ) as fake_dt:
                fake_dt.now.return_value = datetime(2026, 3, 20, 10, 1, 0, tzinfo=timezone.utc)
                fake_dt.fromtimestamp.side_effect = lambda *a, **kw: datetime.fromtimestamp(*a, **kw)
                fake_dt.fromisoformat.side_effect = lambda *a, **kw: datetime.fromisoformat(*a, **kw)
                low_payload = commands.worker_status_payload(
                    Path("/tmp/central.db"), paths, task_id=None, recent_limit=5, recent_hours=24.0
                )

        self.assertEqual(low_payload["summary"]["overall_status"], "healthy")
        self.assertGreater(low_payload["summary"]["low_activity_count"], 0)
        self.assertIn("quiet", low_payload["summary"]["headline"])

    def test_command_self_check_uses_stub_worker_and_returns_zero(self) -> None:
        args = argparse.Namespace()
        fake_dispatcher = mock.MagicMock()
        fake_dispatcher.run_once.return_value = 0

        with mock.patch("central_runtime_v2.commands.task_db.connect") as fake_connect, mock.patch(
            "central_runtime_v2.commands.task_db.apply_migrations"
        ), mock.patch(
            "central_runtime_v2.commands.task_db.load_migrations", return_value=[]
        ), mock.patch(
            "central_runtime_v2.commands.task_db.resolve_migrations_dir", return_value=Path("/tmp/mig")
        ), mock.patch(
            "central_runtime_v2.commands.task_db.ensure_repo"
        ), mock.patch(
            "central_runtime_v2.commands.task_db.create_task"
        ), mock.patch(
            "central_runtime_v2.commands.CentralDispatcher", return_value=fake_dispatcher
        ), mock.patch(
            "central_runtime_v2.commands.connect_initialized"
        ) as fake_ci:
            fake_conn = mock.MagicMock()
            fake_task_row = mock.MagicMock()
            fake_task_row.__getitem__ = lambda self, key: {"planner_status": "done", "runtime_status": "done", "last_runtime_error": None}[key]
            fake_conn.execute.return_value.fetchone.return_value = fake_task_row
            fake_ci.return_value = fake_conn
            fake_connect.return_value.__enter__ = lambda s: s
            fake_connect.return_value.__exit__ = mock.MagicMock(return_value=False)
            fake_connect.return_value.close = mock.MagicMock()

            out = StringIO()
            with mock.patch("sys.stdout", out):
                rc = commands.command_self_check(args)

        self.assertEqual(rc, 0)
        output = json.loads(out.getvalue())
        self.assertIn("db_path", output)
        self.assertIn("state_dir", output)


if __name__ == "__main__":
    unittest.main()
