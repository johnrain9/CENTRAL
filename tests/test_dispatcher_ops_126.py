#!/usr/bin/env python3
"""Coverage-gap tests for dispatcher_control.py (CENTRAL-OPS-126).

Targets: start/stop/restart/status command paths, argument parsing/validation,
kill-task flows, error handling, config persistence, resolve_max_workers,
resolve_worker_model edge cases, show_config, show_repo_config, main dispatch.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dispatcher_control


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmp_db() -> tuple[str, tempfile.TemporaryDirectory]:
    """Return (db_path, tmpdir) with the minimal repos table for tests."""
    tmpdir = tempfile.TemporaryDirectory(prefix="dispatcher_ops_126_")
    db_path = str(Path(tmpdir.name) / "tasks.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE repos (repo_id TEXT PRIMARY KEY, metadata_json TEXT)"
    )
    conn.execute(
        "INSERT INTO repos VALUES ('CENTRAL', '{\"max_concurrent_workers\": 2}')"
    )
    conn.execute("INSERT INTO repos VALUES ('AIM', NULL)")
    conn.commit()
    conn.close()
    return db_path, tmpdir


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

class BuildParserTest(unittest.TestCase):
    def test_build_parser_registers_all_subcommands(self) -> None:
        parser = dispatcher_control.build_parser()
        self.assertIsNotNone(parser)
        # parse each known command without error (or just check _subparsers)
        subcommands = {
            "start", "restart", "stop", "status", "workers", "worker-status",
            "logs", "follow", "once", "run-once", "run_once", "check", "menu",
            "config", "kill-task", "repo-config",
        }
        # Parse with each subcommand (no extra args needed for simple ones)
        for cmd in ("stop", "status", "logs", "follow", "once", "run-once",
                    "run_once", "check", "menu"):
            args = parser.parse_args([cmd])
            self.assertEqual(args.command, cmd)

    def test_start_parser_flags(self) -> None:
        parser = dispatcher_control.build_parser()
        args = parser.parse_args(["start", "--max-workers", "3", "--worker-model",
                                   "gpt-5.3-codex", "--worker-mode", "stub", "--notify"])
        self.assertEqual(args.max_workers, 3)
        self.assertEqual(args.worker_model, "gpt-5.3-codex")
        self.assertEqual(args.worker_mode, "stub")
        self.assertTrue(args.notify)

    def test_restart_parser_no_notify_flag(self) -> None:
        parser = dispatcher_control.build_parser()
        args = parser.parse_args(["restart", "--no-notify"])
        self.assertFalse(args.notify)

    def test_kill_task_parser(self) -> None:
        parser = dispatcher_control.build_parser()
        args = parser.parse_args(["kill-task", "CENTRAL-99", "--reason", "test", "--json"])
        self.assertEqual(args.task_id, "CENTRAL-99")
        self.assertEqual(args.reason, "test")
        self.assertTrue(args.json)

    def test_repo_config_parser(self) -> None:
        parser = dispatcher_control.build_parser()
        args = parser.parse_args(["repo-config", "--repo", "CENTRAL", "--max-workers", "5", "--json"])
        self.assertEqual(args.repo, "CENTRAL")
        self.assertEqual(args.max_workers, 5)
        self.assertTrue(args.json)

    def test_config_parser_audit_model(self) -> None:
        parser = dispatcher_control.build_parser()
        args = parser.parse_args(["config", "--audit-model", "gpt-5.3-codex"])
        self.assertEqual(args.audit_model, "gpt-5.3-codex")

    def test_argparse_positive_int_in_start_rejects_zero(self) -> None:
        parser = dispatcher_control.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["start", "--max-workers", "0"])


# ---------------------------------------------------------------------------
# validate_model_for_mode
# ---------------------------------------------------------------------------

class ValidateModelForModeTest(unittest.TestCase):
    def test_valid_codex_mode_passes(self) -> None:
        # Should not raise
        dispatcher_control.validate_model_for_mode("gpt-5.3-codex", "codex")

    def test_invalid_codex_model_dies(self) -> None:
        with self.assertRaises(SystemExit):
            dispatcher_control.validate_model_for_mode("claude-sonnet-4-6", "codex")

    def test_non_codex_mode_accepts_any_model(self) -> None:
        # claude mode has no validation gate in validate_model_for_mode
        dispatcher_control.validate_model_for_mode("claude-sonnet-4-6", "claude")


# ---------------------------------------------------------------------------
# resolve_effort
# ---------------------------------------------------------------------------

class ResolveEffortTest(unittest.TestCase):
    def test_default_effort(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != dispatcher_control.CODEX_EFFORT_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            raw, source = dispatcher_control.resolve_effort()
        self.assertEqual(source, "default")

    def test_env_effort_valid(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.CODEX_EFFORT_ENV: "medium"}):
            raw, source = dispatcher_control.resolve_effort()
        self.assertEqual(raw, "medium")
        self.assertEqual(source, "env")

    def test_env_effort_invalid_dies(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.CODEX_EFFORT_ENV: "turbo"}):
            with self.assertRaises(SystemExit):
                dispatcher_control.resolve_effort()

    def test_empty_env_effort_falls_back_to_default(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.CODEX_EFFORT_ENV: ""}):
            raw, source = dispatcher_control.resolve_effort()
        self.assertEqual(source, "default")


# ---------------------------------------------------------------------------
# env_max_workers / env_codex_model / env_worker_model
# ---------------------------------------------------------------------------

class EnvResolversTest(unittest.TestCase):
    def test_env_max_workers_set(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.MAX_WORKERS_ENV: "4"}):
            self.assertEqual(dispatcher_control.env_max_workers(), 4)

    def test_env_max_workers_invalid_dies(self) -> None:
        with mock.patch.dict(os.environ, {dispatcher_control.MAX_WORKERS_ENV: "bad"}):
            with self.assertRaises(SystemExit):
                dispatcher_control.env_max_workers()

    def test_env_max_workers_not_set(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != dispatcher_control.MAX_WORKERS_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(dispatcher_control.env_max_workers())

    def test_env_codex_model_not_set(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != dispatcher_control.CODEX_MODEL_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(dispatcher_control.env_codex_model())

    def test_env_worker_model_not_set(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != dispatcher_control.WORKER_MODEL_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(dispatcher_control.env_worker_model())


# ---------------------------------------------------------------------------
# resolve_max_workers edge cases
# ---------------------------------------------------------------------------

class ResolveMaxWorkersEdgeCaseTest(unittest.TestCase):
    def test_cli_value_takes_precedence(self) -> None:
        result = dispatcher_control.resolve_max_workers(5, restart=False)
        self.assertEqual(result.value, 5)
        self.assertEqual(result.source, "cli")

    def test_restart_uses_running_daemon_value(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_max_workers", return_value=None
        ), mock.patch.object(
            dispatcher_control, "running_lock_payload",
            return_value={"max_workers": 3},
        ):
            result = dispatcher_control.resolve_max_workers(None, restart=True)
        self.assertEqual(result.value, 3)
        self.assertEqual(result.source, "running_daemon")

    def test_restart_no_running_daemon_falls_back_to_saved(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_max_workers", return_value=None
        ), mock.patch.object(
            dispatcher_control, "running_lock_payload", return_value=None
        ), mock.patch.object(
            dispatcher_control, "saved_max_workers", return_value=7
        ):
            result = dispatcher_control.resolve_max_workers(None, restart=True)
        self.assertEqual(result.value, 7)
        self.assertEqual(result.source, "saved_config")

    def test_no_restart_skips_running_daemon(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_max_workers", return_value=None
        ), mock.patch.object(
            dispatcher_control, "saved_max_workers", return_value=None
        ):
            result = dispatcher_control.resolve_max_workers(None, restart=False)
        self.assertEqual(result.value, dispatcher_control.DEFAULT_MAX_WORKERS)
        self.assertEqual(result.source, "default")


# ---------------------------------------------------------------------------
# resolve_worker_model edge cases
# ---------------------------------------------------------------------------

class ResolveWorkerModelEdgeCaseTest(unittest.TestCase):
    def test_cli_value_takes_precedence(self) -> None:
        result = dispatcher_control.resolve_worker_model("gpt-5.3-codex", restart=False)
        self.assertEqual(result.source, "cli")

    def test_env_generic_takes_precedence_over_codex_env(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_worker_model", return_value="gpt-5.3-codex"
        ), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value="other-codex"
        ):
            result = dispatcher_control.resolve_worker_model(None, restart=False)
        self.assertEqual(result.source, "model_env")
        self.assertEqual(result.value, "gpt-5.3-codex")

    def test_codex_env_used_when_no_generic_env(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_worker_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value="gpt-5.3-codex"
        ):
            result = dispatcher_control.resolve_worker_model(None, restart=False)
        self.assertEqual(result.source, "model_env")

    def test_restart_uses_running_daemon_model(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_worker_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "running_lock_payload",
            return_value={"default_worker_model": "gpt-5.4"},
        ):
            result = dispatcher_control.resolve_worker_model(None, restart=True)
        self.assertEqual(result.source, "running_daemon")
        self.assertEqual(result.value, "gpt-5.4")

    def test_restart_falls_back_to_default_when_no_running_daemon(self) -> None:
        with mock.patch.object(
            dispatcher_control, "env_worker_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "running_lock_payload", return_value={}
        ), mock.patch.object(
            dispatcher_control, "saved_worker_model", return_value=None
        ):
            result = dispatcher_control.resolve_worker_model(None, restart=True)
        self.assertEqual(result.source, "default")

    def test_resolve_codex_model_delegates_to_resolve_worker_model(self) -> None:
        with mock.patch.object(
            dispatcher_control, "resolve_worker_model",
            return_value=dispatcher_control.ResolvedWorkerModel("x", "env"),
        ) as m:
            result = dispatcher_control.resolve_codex_model("y", restart=False)
        m.assert_called_once_with("y", restart=False)
        self.assertEqual(result.value, "x")


# ---------------------------------------------------------------------------
# describe_source
# ---------------------------------------------------------------------------

class DescribeSourceTest(unittest.TestCase):
    def test_known_sources(self) -> None:
        self.assertEqual(dispatcher_control.describe_source("cli"), "cli flag")
        self.assertEqual(dispatcher_control.describe_source("default"), "default")
        self.assertEqual(dispatcher_control.describe_source("running_daemon"), "running daemon")

    def test_unknown_source_returns_value(self) -> None:
        self.assertEqual(dispatcher_control.describe_source("mystery"), "mystery")


# ---------------------------------------------------------------------------
# init_db error path
# ---------------------------------------------------------------------------

class InitDbTest(unittest.TestCase):
    def test_init_db_failure_dies(self) -> None:
        with mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="db fail")), \
             mock.patch.object(dispatcher_control, "STATE_DIR", Path("/tmp")):
            with self.assertRaises(SystemExit):
                dispatcher_control.init_db()


# ---------------------------------------------------------------------------
# start_dispatcher
# ---------------------------------------------------------------------------

class StartDispatcherTest(unittest.TestCase):
    def _base_patches(self):
        return [
            mock.patch.object(dispatcher_control, "ensure_runtime"),
            mock.patch.object(dispatcher_control, "init_db"),
            mock.patch.object(dispatcher_control, "running_pid", return_value=None),
            mock.patch.object(
                dispatcher_control, "resolve_max_workers",
                return_value=dispatcher_control.ResolvedMaxWorkers(1, "default"),
            ),
            mock.patch.object(
                dispatcher_control, "resolve_worker_model",
                return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
            ),
            mock.patch.object(dispatcher_control, "saved_worker_mode", return_value="stub"),
            mock.patch.object(dispatcher_control, "saved_notify", return_value=False),
            mock.patch.object(dispatcher_control, "saved_audit_model", return_value=None),
            mock.patch.object(dispatcher_control, "saved_max_workers", return_value=None),
            mock.patch.object(dispatcher_control, "saved_worker_model", return_value=None),
            mock.patch.object(dispatcher_control, "validate_model_for_mode"),
        ]

    def test_start_success(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None
        runtime_payload = {
            "configured_max_workers": 1,
            "configured_default_worker_model": "gpt-5.3-codex",
            "worker_mode": "stub",
        }

        patches = self._base_patches()
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_log = Path(tmpdir) / "launcher.log"

            pid_seq = iter([None, None, os.getpid()])  # pid becomes available on 3rd call

            def pid_side_effect():
                return next(pid_seq)

            all_patches = patches + [
                mock.patch.object(dispatcher_control, "LAUNCH_LOG_PATH", launch_log),
                mock.patch.object(dispatcher_control, "LOG_PATH", Path(tmpdir) / "d.log"),
                mock.patch("dispatcher_control.subprocess.Popen", return_value=fake_proc),
                mock.patch.object(dispatcher_control, "running_pid", side_effect=pid_side_effect),
                mock.patch.object(dispatcher_control, "runtime_status_payload", return_value=runtime_payload),
                mock.patch("dispatcher_control.time.time", side_effect=[0.0, 0.5, 1.0]),
                mock.patch("dispatcher_control.time.sleep"),
            ]

            with contextlib.ExitStack() as stack:
                for p in all_patches:
                    stack.enter_context(p)
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.start_dispatcher(
                        restart=False,
                        max_workers=1,
                        worker_model="gpt-5.3-codex",
                        worker_mode="stub",
                        notify=False,
                    )
            self.assertEqual(rc, 0)
            self.assertIn("Dispatcher started", stdout.getvalue())

    def test_start_already_running_no_restart(self) -> None:
        patches = self._base_patches()
        # Override running_pid to return a live pid
        patches_no_pid = [p for p in patches if not isinstance(getattr(p, "attribute", None), str)]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            # Override running_pid
            stack.enter_context(
                mock.patch.object(dispatcher_control, "running_pid", return_value=os.getpid())
            )
            stack.enter_context(
                mock.patch.object(dispatcher_control, "print_status", return_value=0)
            )
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.start_dispatcher(restart=False)
        self.assertEqual(rc, 0)
        self.assertIn("already running", stdout.getvalue())

    def test_start_already_running_reports_model_message(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._base_patches():
                stack.enter_context(p)
            stack.enter_context(
                mock.patch.object(dispatcher_control, "running_pid", return_value=os.getpid())
            )
            stack.enter_context(
                mock.patch.object(dispatcher_control, "print_status", return_value=0)
            )
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                dispatcher_control.start_dispatcher(
                    restart=False, max_workers=2, worker_model="gpt-5.3-codex"
                )
            output = stdout.getvalue()
            self.assertIn("restart is required", output)

    def test_restart_calls_stop_first(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            launch_log = Path(tmpdir) / "launcher.log"
            pid_seq = iter([os.getpid(), None, os.getpid()])

            with contextlib.ExitStack() as stack:
                for p in self._base_patches():
                    stack.enter_context(p)
                stop_mock = stack.enter_context(
                    mock.patch.object(dispatcher_control, "stop_dispatcher")
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "running_pid",
                                      side_effect=lambda: next(pid_seq))
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "LAUNCH_LOG_PATH", launch_log)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "LOG_PATH", Path(tmpdir) / "d.log")
                )
                stack.enter_context(
                    mock.patch("dispatcher_control.subprocess.Popen", return_value=fake_proc)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "runtime_status_payload",
                                      return_value={"worker_mode": "stub"})
                )
                stack.enter_context(
                    mock.patch("dispatcher_control.time.time", side_effect=[0.0, 1.0, 2.0])
                )
                stack.enter_context(mock.patch("dispatcher_control.time.sleep"))
                with mock.patch("sys.stdout", io.StringIO()):
                    dispatcher_control.start_dispatcher(restart=True)
            stop_mock.assert_called_once_with(quiet=True)

    def test_start_proc_exits_early_dies(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = 1  # Process exited immediately

        with tempfile.TemporaryDirectory() as tmpdir:
            launch_log = Path(tmpdir) / "launcher.log"
            launch_log.write_text("launch error\n", encoding="utf-8")

            with contextlib.ExitStack() as stack:
                for p in self._base_patches():
                    stack.enter_context(p)
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "running_pid", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "LAUNCH_LOG_PATH", launch_log)
                )
                stack.enter_context(
                    mock.patch("dispatcher_control.subprocess.Popen", return_value=fake_proc)
                )
                stack.enter_context(
                    mock.patch("dispatcher_control.time.time", side_effect=[0.0, 0.5])
                )
                stack.enter_context(mock.patch("dispatcher_control.time.sleep"))
                with self.assertRaises(SystemExit):
                    dispatcher_control.start_dispatcher()

    def test_start_timeout_dies(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            launch_log = Path(tmpdir) / "launcher.log"

            with contextlib.ExitStack() as stack:
                for p in self._base_patches():
                    stack.enter_context(p)
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "running_pid", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "LAUNCH_LOG_PATH", launch_log)
                )
                stack.enter_context(
                    mock.patch("dispatcher_control.subprocess.Popen", return_value=fake_proc)
                )
                # time.time returns values that exhaust the deadline
                stack.enter_context(
                    mock.patch("dispatcher_control.time.time",
                               side_effect=[0.0, 11.0, 11.5])
                )
                stack.enter_context(mock.patch("dispatcher_control.time.sleep"))
                with self.assertRaises(SystemExit):
                    dispatcher_control.start_dispatcher()


# ---------------------------------------------------------------------------
# stop_dispatcher
# ---------------------------------------------------------------------------

class StopDispatcherTest(unittest.TestCase):
    def test_stop_success(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(dispatcher_control, "running_pid", return_value=None), \
             mock.patch("dispatcher_control.time.time", side_effect=[0.0, 0.5]):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.stop_dispatcher()
            self.assertEqual(rc, 0)
            self.assertIn("stopped", stdout.getvalue())

    def test_stop_quiet(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(dispatcher_control, "running_pid", return_value=None), \
             mock.patch("dispatcher_control.time.time", side_effect=[0.0, 0.5]):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.stop_dispatcher(quiet=True)
            self.assertEqual(rc, 0)
            self.assertEqual(stdout.getvalue(), "")

    def test_stop_subprocess_failure_dies(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="stop fail")):
            with self.assertRaises(SystemExit):
                dispatcher_control.stop_dispatcher()

    def test_stop_timeout_dies(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(dispatcher_control, "running_pid",
                               return_value=os.getpid()), \
             mock.patch("dispatcher_control.time.time",
                        side_effect=[0.0, 5.0, 11.0]), \
             mock.patch("dispatcher_control.time.sleep"):
            with self.assertRaises(SystemExit):
                dispatcher_control.stop_dispatcher()


# ---------------------------------------------------------------------------
# print_status
# ---------------------------------------------------------------------------

class PrintStatusTest(unittest.TestCase):
    def test_print_status_outputs_json(self) -> None:
        payload = {"running": False, "configured_max_workers": 2}
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch.object(dispatcher_control, "init_db"), \
             mock.patch.object(dispatcher_control, "launcher_status_payload",
                               return_value=payload):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.print_status()
            self.assertEqual(rc, 0)
            out = json.loads(stdout.getvalue())
            self.assertFalse(out["running"])


# ---------------------------------------------------------------------------
# show_config
# ---------------------------------------------------------------------------

class ShowConfigTest(unittest.TestCase):
    def _resolve_patches(self):
        return [
            mock.patch.object(dispatcher_control, "ensure_runtime"),
            mock.patch.object(
                dispatcher_control, "resolve_worker_model",
                return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
            ),
            mock.patch.object(
                dispatcher_control, "resolve_max_workers",
                return_value=dispatcher_control.ResolvedMaxWorkers(1, "default"),
            ),
            mock.patch.object(dispatcher_control, "env_max_workers", return_value=None),
            mock.patch.object(dispatcher_control, "env_worker_model", return_value=None),
            mock.patch.object(dispatcher_control, "env_codex_model", return_value=None),
        ]

    def test_show_config_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "dispatcher-config.json"
            state_dir = Path(tmpdir)
            with contextlib.ExitStack() as stack:
                for p in self._resolve_patches():
                    stack.enter_context(p)
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "CONFIG_PATH", config_path)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "STATE_DIR", state_dir)
                )
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_config()
            self.assertEqual(rc, 0)
            out = json.loads(stdout.getvalue())
            self.assertIn("config_path", out)

    def test_show_config_saves_when_args_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "dispatcher-config.json"
            state_dir = Path(tmpdir)
            with contextlib.ExitStack() as stack:
                for p in self._resolve_patches():
                    stack.enter_context(p)
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "CONFIG_PATH", config_path)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "STATE_DIR", state_dir)
                )
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_config(max_workers=3, worker_mode="stub")
            self.assertEqual(rc, 0)
            saved = json.loads(config_path.read_text())
            self.assertEqual(saved["max_workers"], 3)
            self.assertEqual(saved["worker_mode"], "stub")


# ---------------------------------------------------------------------------
# show_repo_config
# ---------------------------------------------------------------------------

class ShowRepoConfigTest(unittest.TestCase):
    def test_show_all_repos_json(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_repo_config(as_json=True)
                self.assertEqual(rc, 0)
                result = json.loads(stdout.getvalue())
                repo_ids = [r["repo_id"] for r in result]
                self.assertIn("CENTRAL", repo_ids)
        finally:
            tmpdir.cleanup()

    def test_show_specific_repo_text(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_repo_config(repo="CENTRAL")
                self.assertEqual(rc, 0)
                self.assertIn("CENTRAL", stdout.getvalue())
        finally:
            tmpdir.cleanup()

    def test_set_max_workers_for_repo(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_repo_config(
                        repo="CENTRAL", max_workers=5, as_json=True
                    )
                self.assertEqual(rc, 0)
                # Output has "Set CENTRAL..." line followed by JSON
                raw = stdout.getvalue()
                json_start = raw.index("[")
                result = json.loads(raw[json_start:])
                central = next(r for r in result if r["repo_id"] == "CENTRAL")
                self.assertEqual(central["max_concurrent_workers"], 5)
        finally:
            tmpdir.cleanup()

    def test_set_max_workers_requires_repo(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = dispatcher_control.show_repo_config(max_workers=5)
                self.assertEqual(rc, 1)
        finally:
            tmpdir.cleanup()

    def test_set_max_workers_unknown_repo_returns_1(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                rc = dispatcher_control.show_repo_config(repo="NONEXISTENT", max_workers=2)
            self.assertEqual(rc, 1)
        finally:
            tmpdir.cleanup()

    def test_repo_with_null_metadata_uses_default(self) -> None:
        db_path, tmpdir = _make_tmp_db()
        try:
            with mock.patch.object(dispatcher_control, "_effective_db_path",
                                   return_value=db_path):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = dispatcher_control.show_repo_config(repo="AIM", as_json=True)
                self.assertEqual(rc, 0)
                result = json.loads(stdout.getvalue())
                aim = result[0]
                self.assertEqual(aim["max_concurrent_workers"],
                                  dispatcher_control.DEFAULT_REPO_MAX_CONCURRENT_WORKERS)
                self.assertTrue(aim["is_default"])
        finally:
            tmpdir.cleanup()


# ---------------------------------------------------------------------------
# kill_task
# ---------------------------------------------------------------------------

class KillTaskTest(unittest.TestCase):
    def _base_kill_patches(self, payload: dict):
        return [
            mock.patch.object(dispatcher_control, "ensure_runtime"),
            mock.patch.object(dispatcher_control, "init_db"),
            mock.patch(
                "dispatcher_control.subprocess.run",
                return_value=mock.Mock(
                    returncode=0,
                    stdout=json.dumps(payload),
                    stderr="",
                ),
            ),
            mock.patch.object(
                dispatcher_control, "terminate_worker",
                return_value={"attempted": False, "terminated": False, "worker_present": False},
            ),
        ]

    def test_kill_task_json_output(self) -> None:
        payload = {
            "task_id": "CENTRAL-42",
            "reason": "test",
            "snapshot": {"planner_status": "failed", "runtime": {"runtime_status": "failed"}},
        }
        with contextlib.ExitStack() as stack:
            for p in self._base_kill_patches(payload):
                stack.enter_context(p)
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.kill_task(
                    task_id="CENTRAL-42", reason="test", as_json=True
                )
        self.assertEqual(rc, 0)
        out = json.loads(stdout.getvalue())
        self.assertEqual(out["task_id"], "CENTRAL-42")

    def test_kill_task_text_output_no_worker(self) -> None:
        payload = {"task_id": "CENTRAL-42", "reason": "test"}
        with contextlib.ExitStack() as stack:
            for p in self._base_kill_patches(payload):
                stack.enter_context(p)
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.kill_task(
                    task_id="CENTRAL-42", reason="test", as_json=False
                )
        self.assertEqual(rc, 0)
        self.assertIn("CENTRAL-42", stdout.getvalue())
        self.assertIn("no active worker", stdout.getvalue())

    def test_kill_task_text_output_with_worker_terminated(self) -> None:
        payload = {"task_id": "CENTRAL-42", "reason": "test"}
        with contextlib.ExitStack() as stack:
            for p in self._base_kill_patches(payload):
                stack.enter_context(p)
            # Override terminate_worker to show a terminated worker
            stack.enter_context(
                mock.patch.object(
                    dispatcher_control, "terminate_worker",
                    return_value={
                        "attempted": True,
                        "terminated": True,
                        "worker_present": True,
                        "pid": 9999,
                    },
                )
            )
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                dispatcher_control.kill_task(
                    task_id="CENTRAL-42", reason="test", as_json=False
                )
        self.assertIn("terminated", stdout.getvalue())

    def test_kill_task_subprocess_failure_dies(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch.object(dispatcher_control, "init_db"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="error")):
            with self.assertRaises(SystemExit):
                dispatcher_control.kill_task(
                    task_id="CENTRAL-1", reason="test", as_json=False
                )

    def test_kill_task_invalid_json_dies(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch.object(dispatcher_control, "init_db"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="{bad", stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control.kill_task(
                    task_id="CENTRAL-1", reason="test", as_json=False
                )

    def test_kill_task_non_object_json_dies(self) -> None:
        with mock.patch.object(dispatcher_control, "ensure_runtime"), \
             mock.patch.object(dispatcher_control, "init_db"), \
             mock.patch("dispatcher_control.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout='["list"]', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control.kill_task(
                    task_id="CENTRAL-1", reason="test", as_json=False
                )


# ---------------------------------------------------------------------------
# main() dispatch paths
# ---------------------------------------------------------------------------

class MainDispatchTest(unittest.TestCase):
    """Test that main() routes each subcommand to the right function."""

    def _run_main(self, argv: list[str], mock_target: str, return_value: int = 0) -> int:
        with mock.patch.object(dispatcher_control, mock_target, return_value=return_value) as m:
            rc = dispatcher_control.main(["dispatcher_control.py"] + argv)
        return rc

    def test_start_command(self) -> None:
        rc = self._run_main(["start"], "start_dispatcher")
        self.assertEqual(rc, 0)

    def test_restart_command(self) -> None:
        rc = self._run_main(["restart"], "start_dispatcher")
        self.assertEqual(rc, 0)

    def test_stop_command(self) -> None:
        rc = self._run_main(["stop"], "stop_dispatcher")
        self.assertEqual(rc, 0)

    def test_status_command(self) -> None:
        rc = self._run_main(["status"], "print_status")
        self.assertEqual(rc, 0)

    def test_logs_command(self) -> None:
        rc = self._run_main(["logs"], "show_logs")
        self.assertEqual(rc, 0)

    def test_once_command(self) -> None:
        rc = self._run_main(["once"], "run_once")
        self.assertEqual(rc, 0)

    def test_run_once_command(self) -> None:
        rc = self._run_main(["run-once"], "run_once")
        self.assertEqual(rc, 0)

    def test_run_once_underscore_command(self) -> None:
        rc = self._run_main(["run_once"], "run_once")
        self.assertEqual(rc, 0)

    def test_check_command(self) -> None:
        rc = self._run_main(["check"], "run_check")
        self.assertEqual(rc, 0)

    def test_menu_command(self) -> None:
        rc = self._run_main(["menu"], "run_menu")
        self.assertEqual(rc, 0)

    def test_config_command(self) -> None:
        rc = self._run_main(["config"], "show_config")
        self.assertEqual(rc, 0)

    def test_kill_task_command(self) -> None:
        with mock.patch.object(dispatcher_control, "kill_task", return_value=0) as m:
            rc = dispatcher_control.main(
                ["dispatcher_control.py", "kill-task", "CENTRAL-1", "--json"]
            )
        self.assertEqual(rc, 0)
        m.assert_called_once_with(task_id="CENTRAL-1", reason="operator kill requested", as_json=True)

    def test_repo_config_command(self) -> None:
        with mock.patch.object(dispatcher_control, "show_repo_config", return_value=0) as m:
            rc = dispatcher_control.main(
                ["dispatcher_control.py", "repo-config", "--repo", "CENTRAL", "--json"]
            )
        self.assertEqual(rc, 0)
        m.assert_called_once_with(repo="CENTRAL", max_workers=None, as_json=True)

    def test_workers_command(self) -> None:
        with mock.patch.object(dispatcher_control, "show_workers", return_value=0) as m:
            rc = dispatcher_control.main(
                ["dispatcher_control.py", "workers", "--json", "--limit", "3"]
            )
        self.assertEqual(rc, 0)
        m.assert_called_once_with(as_json=True, task_id=None, limit=3, recent_hours=24.0)

    def test_worker_status_command(self) -> None:
        with mock.patch.object(dispatcher_control, "show_workers", return_value=0) as m:
            rc = dispatcher_control.main(
                ["dispatcher_control.py", "worker-status"]
            )
        self.assertEqual(rc, 0)

    def test_no_command_defaults_to_start(self) -> None:
        # Default command is "start" when no subcommand given (cmd = args.command or "start")
        with mock.patch.object(dispatcher_control, "start_dispatcher", return_value=0) as m:
            rc = dispatcher_control.main(["dispatcher_control.py"])
        self.assertEqual(rc, 0)
        m.assert_called_once()


# ---------------------------------------------------------------------------
# run_menu additional paths
# ---------------------------------------------------------------------------

class RunMenuAdditionalPathsTest(unittest.TestCase):
    def _menu_patches(self):
        return [
            mock.patch.object(dispatcher_control, "ensure_runtime"),
            mock.patch.object(dispatcher_control, "init_db"),
            mock.patch.object(
                dispatcher_control, "resolve_max_workers",
                return_value=dispatcher_control.ResolvedMaxWorkers(1, "default"),
            ),
            mock.patch.object(
                dispatcher_control, "resolve_worker_model",
                return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
            ),
            mock.patch.object(dispatcher_control, "saved_worker_mode", return_value="stub"),
            mock.patch.object(dispatcher_control, "saved_notify", return_value=False),
            mock.patch.object(dispatcher_control, "running_pid", return_value=None),
        ]

    def test_menu_stop_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            stop_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "stop_dispatcher", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["2", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        stop_mock.assert_called_once()

    def test_menu_status_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            status_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "print_status", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["5", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        status_mock.assert_called_once()

    def test_menu_logs_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            logs_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "show_logs", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["7", "8", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        self.assertEqual(logs_mock.call_count, 2)

    def test_menu_run_once_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            run_once_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "run_once", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["9", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        run_once_mock.assert_called_once()

    def test_menu_check_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            check_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "run_check", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["10", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        check_mock.assert_called_once()

    def test_menu_kill_task_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            kt_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "run_kill_task_prompt", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["11", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        kt_mock.assert_called_once()

    def test_menu_repo_config_choice(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            rc_mock = stack.enter_context(
                mock.patch.object(dispatcher_control, "show_repo_config", return_value=0)
            )
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["12", "0"])
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        rc_mock.assert_called_once()

    def test_menu_invalid_choice_prints_error(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            stack.enter_context(
                mock.patch("builtins.input", side_effect=["99", "0"])
            )
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)
        self.assertIn("Invalid menu option", stdout.getvalue())

    def test_menu_eof_exits_cleanly(self) -> None:
        with contextlib.ExitStack() as stack:
            for p in self._menu_patches():
                stack.enter_context(p)
            stack.enter_context(
                mock.patch("builtins.input", side_effect=EOFError)
            )
            with mock.patch("sys.stdout", io.StringIO()):
                rc = dispatcher_control.run_menu()
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# prompt_worker_model_select retry
# ---------------------------------------------------------------------------

class PromptWorkerModelSelectTest(unittest.TestCase):
    def test_retry_on_invalid_then_valid(self) -> None:
        with mock.patch("builtins.input", side_effect=["99", "1"]):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                result = dispatcher_control.prompt_worker_model_select(
                    "Model", "gpt-5.3-codex", "codex"
                )
        self.assertIn("Invalid selection", stdout.getvalue())
        self.assertIsNotNone(result)

    def test_claude_mode_uses_claude_models(self) -> None:
        with mock.patch("builtins.input", side_effect=["1"]):
            with mock.patch("sys.stdout", io.StringIO()):
                result = dispatcher_control.prompt_worker_model_select(
                    "Model", "claude-sonnet-4-6", "claude"
                )
        self.assertIn("claude", result)


# ---------------------------------------------------------------------------
# run_config_update
# ---------------------------------------------------------------------------

class RunConfigUpdateTest(unittest.TestCase):
    def test_run_config_update_saves_and_shows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "tasks.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE repos (repo_id TEXT PRIMARY KEY, metadata_json TEXT)"
            )
            conn.commit()
            conn.close()

            config_path = Path(tmpdir) / "dispatcher-config.json"
            state_dir = Path(tmpdir)

            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "CONFIG_PATH", config_path)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "STATE_DIR", state_dir)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "ensure_runtime")
                )
                stack.enter_context(
                    mock.patch.object(
                        dispatcher_control, "resolve_max_workers",
                        return_value=dispatcher_control.ResolvedMaxWorkers(2, "default"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        dispatcher_control, "resolve_worker_model",
                        return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "saved_worker_mode", return_value="stub")
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "saved_audit_model", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "saved_notify", return_value=False)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "saved_max_workers", return_value=2)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "saved_worker_model", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "env_max_workers", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "env_worker_model", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "env_codex_model", return_value=None)
                )
                stack.enter_context(
                    mock.patch.object(dispatcher_control, "_effective_db_path",
                                      return_value=db_path)
                )
                # Inputs: max_workers=2(default), mode=stub(default "3"), model=1(default),
                # audit=1(default), notify=n, no repo cap
                stack.enter_context(
                    mock.patch("builtins.input", side_effect=["", "3", "1", "1", "n", ""])
                )
                with mock.patch("sys.stdout", io.StringIO()):
                    rc = dispatcher_control.run_config_update()
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
