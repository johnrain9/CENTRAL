#!/usr/bin/env python3
"""Additional dispatcher_control.py behavior coverage."""

from __future__ import annotations

import argparse
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dispatcher_control


class DispatcherControlAdditionalBehaviorTest(unittest.TestCase):
    def test_validate_runtime_and_ensure_runtime_failures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_control_runtime_") as tmpdir:
            tmp_path = Path(tmpdir)
            runtime_script = tmp_path / "runtime.py"
            runtime_script.write_text("print('ok')\n", encoding="utf-8")
            db_script = tmp_path / "db.py"
            db_script.write_text("print('db')\n", encoding="utf-8")

            with mock.patch.object(dispatcher_control, "RUNTIME_SCRIPT", runtime_script), mock.patch.object(
                dispatcher_control, "DB_SCRIPT", db_script
            ), mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
                dispatcher_control.validate_runtime_importable()
                dispatcher_control.ensure_runtime()

            with mock.patch.object(dispatcher_control, "RUNTIME_SCRIPT", tmp_path / "missing.py"):
                with self.assertRaises(SystemExit):
                    dispatcher_control.validate_runtime_importable()

            with mock.patch.object(dispatcher_control, "RUNTIME_SCRIPT", runtime_script), mock.patch(
                "dispatcher_control.subprocess.run",
                return_value=mock.Mock(returncode=1, stdout="", stderr="boom"),
            ):
                with self.assertRaises(SystemExit):
                    dispatcher_control.validate_runtime_importable()

            with mock.patch.object(dispatcher_control, "RUNTIME_SCRIPT", runtime_script), mock.patch.object(
                dispatcher_control, "DB_SCRIPT", tmp_path / "missing-db.py"
            ):
                with self.assertRaises(SystemExit):
                    dispatcher_control.ensure_runtime()

    def test_parse_helpers_and_command_builders(self) -> None:
        self.assertEqual(dispatcher_control.parse_positive_int("4", label="workers"), 4)
        self.assertEqual(dispatcher_control.argparse_positive_int("7"), 7)

        with self.assertRaises(SystemExit):
            dispatcher_control.parse_positive_int("abc", label="workers")
        with self.assertRaises(SystemExit):
            dispatcher_control.parse_positive_int("0", label="workers")
        with self.assertRaises(argparse.ArgumentTypeError):
            dispatcher_control.argparse_positive_int("abc")
        with self.assertRaises(argparse.ArgumentTypeError):
            dispatcher_control.argparse_positive_int("0")

        with mock.patch.object(dispatcher_control, "DB_PATH", "/tmp/test.db"), mock.patch.object(
            dispatcher_control, "STATE_DIR", Path("/tmp/state")
        ):
            self.assertEqual(
                dispatcher_control.runtime_cmd("status"),
                [dispatcher_control.PYTHON_BIN, str(dispatcher_control.RUNTIME_SCRIPT), "status", "--db-path", "/tmp/test.db", "--state-dir", "/tmp/state"],
            )
            self.assertEqual(
                dispatcher_control.db_cmd("init"),
                [dispatcher_control.PYTHON_BIN, str(dispatcher_control.DB_SCRIPT), "init", "--db-path", "/tmp/test.db"],
            )
            self.assertEqual(
                dispatcher_control.db_init_cmd(),
                [dispatcher_control.PYTHON_BIN, str(dispatcher_control.DB_SCRIPT), "init", "--json", "--db-path", "/tmp/test.db"],
            )

    def test_json_lock_and_process_helpers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_control_json_") as tmpdir:
            tmp_path = Path(tmpdir)
            good = tmp_path / "good.json"
            good.write_text('{"pid": 1}\n', encoding="utf-8")
            bad = tmp_path / "bad.json"
            bad.write_text("{oops", encoding="utf-8")
            not_object = tmp_path / "list.json"
            not_object.write_text("[1, 2]\n", encoding="utf-8")

            self.assertEqual(dispatcher_control.read_json_file(good), {"pid": 1})
            self.assertIsNone(dispatcher_control.read_json_file(bad))
            self.assertIsNone(dispatcher_control.read_json_file(not_object))
            self.assertIsNone(dispatcher_control.read_json_file(tmp_path / "missing.json"))

            lock_path = tmp_path / "dispatcher.lock"
            lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            with mock.patch.object(dispatcher_control, "LOCK_PATH", lock_path):
                self.assertEqual(dispatcher_control.read_lock_payload(), {"pid": os.getpid()})
                self.assertEqual(dispatcher_control.running_pid(), os.getpid())
                self.assertEqual(dispatcher_control.running_lock_payload(), {"pid": os.getpid()})

            lock_path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")
            with mock.patch.object(dispatcher_control, "LOCK_PATH", lock_path):
                self.assertIsNone(dispatcher_control.running_pid())
                self.assertIsNone(dispatcher_control.running_lock_payload())

        self.assertFalse(dispatcher_control.pid_alive(None))
        self.assertFalse(dispatcher_control.pid_alive(0))
        self.assertTrue(dispatcher_control.pid_alive(os.getpid()))

        with mock.patch("dispatcher_control.Path.read_text", return_value="123 (python) S " + " ".join(str(i) for i in range(1, 30))):
            self.assertEqual(dispatcher_control.process_start_token(123), "19")
        with mock.patch("dispatcher_control.Path.read_text", return_value="123 python S"):
            self.assertIsNone(dispatcher_control.process_start_token(123))
        with mock.patch("dispatcher_control.Path.read_text", side_effect=OSError):
            self.assertIsNone(dispatcher_control.process_start_token(123))
        self.assertIsNone(dispatcher_control.process_start_token(None))

        with mock.patch.object(dispatcher_control, "pid_alive", return_value=False):
            self.assertFalse(dispatcher_control.process_matches(123, "tok"))
        with mock.patch.object(dispatcher_control, "pid_alive", return_value=True), mock.patch.object(
            dispatcher_control, "process_start_token", return_value="tok"
        ):
            self.assertTrue(dispatcher_control.process_matches(123, "tok"))
            self.assertTrue(dispatcher_control.process_matches(123, None))
            self.assertFalse(dispatcher_control.process_matches(123, "other"))

    def test_terminate_worker_covers_invalid_mismatch_and_forced_paths(self) -> None:
        self.assertEqual(
            dispatcher_control.terminate_worker(None),
            {"attempted": False, "terminated": False, "worker_present": False},
        )
        self.assertEqual(
            dispatcher_control.terminate_worker({"worker_pid": "not-an-int"}),
            {"attempted": False, "terminated": False, "worker_present": False},
        )

        with mock.patch.object(dispatcher_control, "process_matches", return_value=False):
            self.assertEqual(
                dispatcher_control.terminate_worker({"worker_pid": 123, "worker_process_start_token": "tok"}),
                {"attempted": False, "terminated": False, "worker_present": False, "pid": 123},
            )

        with mock.patch.object(dispatcher_control, "process_matches", return_value=True), mock.patch(
            "dispatcher_control.os.kill", side_effect=OSError
        ):
            self.assertEqual(
                dispatcher_control.terminate_worker({"worker_pid": 123, "worker_process_start_token": "tok"}),
                {"attempted": True, "terminated": False, "worker_present": False, "pid": 123},
            )

        with mock.patch.object(dispatcher_control, "process_matches", side_effect=[True, True, True, False]), mock.patch(
            "dispatcher_control.os.kill"
        ) as kill_mock, mock.patch("dispatcher_control.time.time", side_effect=[0.0, 0.1, 5.1, 5.2, 5.25, 5.3]), mock.patch(
            "dispatcher_control.time.sleep"
        ):
            result = dispatcher_control.terminate_worker({"worker_pid": 456, "worker_process_start_token": "tok"})
        self.assertEqual(
            result,
            {"attempted": True, "terminated": True, "worker_present": True, "pid": 456, "forced": True},
        )
        self.assertEqual([call.args for call in kill_mock.call_args_list], [(456, 15), (456, 9)])

    def test_saved_config_round_trip_and_invalid_payloads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_control_config_") as tmpdir:
            config_path = Path(tmpdir) / "dispatcher-config.json"
            state_dir = Path(tmpdir)
            with mock.patch.object(dispatcher_control, "CONFIG_PATH", config_path), mock.patch.object(
                dispatcher_control, "STATE_DIR", state_dir
            ):
                dispatcher_control.save_config(
                    max_workers=3,
                    worker_model="gpt-5.3-codex",
                    worker_mode="stub",
                    notify=True,
                    audit_model="gpt-5.3-codex",
                )
                payload = dispatcher_control.load_saved_config()
                self.assertEqual(payload["max_workers"], 3)
                self.assertEqual(payload["default_worker_model"], "gpt-5.3-codex")
                self.assertEqual(payload["worker_mode"], "stub")
                self.assertEqual(payload["notify"], True)
                self.assertEqual(payload["audit_worker_model"], "gpt-5.3-codex")
                self.assertEqual(dispatcher_control.saved_max_workers(), 3)
                self.assertEqual(dispatcher_control.saved_worker_model(), "gpt-5.3-codex")
                self.assertEqual(dispatcher_control.saved_codex_model(), "gpt-5.3-codex")
                self.assertEqual(dispatcher_control.saved_worker_mode(), "stub")
                self.assertEqual(dispatcher_control.saved_notify(), True)
                self.assertEqual(dispatcher_control.saved_audit_model(), "gpt-5.3-codex")

                config_path.write_text('{"default_codex_model":"gpt-5.3-codex","max_workers":"2"}\n', encoding="utf-8")
                alias_payload = dispatcher_control.load_saved_config()
                self.assertEqual(alias_payload["default_worker_model"], "gpt-5.3-codex")
                self.assertEqual(alias_payload["max_workers"], 2)

                config_path.write_text('["not", "an", "object"]\n', encoding="utf-8")
                with self.assertRaises(SystemExit):
                    dispatcher_control.load_saved_config()

                config_path.write_text("{oops", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    dispatcher_control.load_saved_config()

        with self.assertRaises(SystemExit):
            dispatcher_control.save_config(worker_mode="invalid")

    def test_runtime_status_and_launcher_payload_error_handling(self) -> None:
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='{"running": true}', stderr="")):
            self.assertEqual(dispatcher_control.runtime_status_payload(), {"running": True})

        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='["bad"]', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control.runtime_status_payload()

        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout="{oops", stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control.runtime_status_payload()

        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="status failed")):
            with self.assertRaises(SystemExit):
                dispatcher_control.runtime_status_payload()

        with mock.patch.object(dispatcher_control, "runtime_status_payload", return_value={"running": True}), mock.patch.object(
            dispatcher_control, "resolve_max_workers",
            side_effect=[
                dispatcher_control.ResolvedMaxWorkers(2, "saved_config"),
                dispatcher_control.ResolvedMaxWorkers(3, "running_daemon"),
            ],
        ), mock.patch.object(
            dispatcher_control, "resolve_codex_model",
            side_effect=[
                dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "saved_config"),
                dispatcher_control.ResolvedWorkerModel("gpt-5.4", "running_daemon"),
            ],
        ), mock.patch.object(
            dispatcher_control, "saved_max_workers", return_value=2
        ), mock.patch.object(
            dispatcher_control, "env_max_workers", return_value=None
        ), mock.patch.object(
            dispatcher_control, "saved_worker_model", return_value="gpt-5.3-codex"
        ), mock.patch.object(
            dispatcher_control, "saved_codex_model", return_value="gpt-5.3-codex"
        ), mock.patch.object(
            dispatcher_control, "env_worker_model", return_value=None
        ), mock.patch.object(
            dispatcher_control, "env_codex_model", return_value=None
        ):
            payload = dispatcher_control.launcher_status_payload()
        self.assertEqual(payload["next_start_max_workers"], 2)
        self.assertEqual(payload["next_restart_max_workers"], 3)
        self.assertEqual(payload["next_restart_default_worker_model"], "gpt-5.4")

    def test_output_commands_and_logs(self) -> None:
        daemon_log = mock.Mock()
        daemon_log.colorize_log_line.side_effect = lambda line: f"COLOR:{line}"

        with tempfile.TemporaryDirectory(prefix="dispatcher_control_logs_") as tmpdir:
            log_path = Path(tmpdir) / "dispatcher.log"
            log_path.write_text("line1\nline2\n", encoding="utf-8")

            with mock.patch.object(dispatcher_control, "ensure_runtime"), mock.patch.object(
                dispatcher_control, "init_db"
            ), mock.patch(
                "dispatcher_control.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="runtime ok\n", stderr=""),
            ), mock.patch.object(
                dispatcher_control, "saved_worker_mode", return_value="stub"
            ), mock.patch.object(
                dispatcher_control, "resolve_worker_model",
                return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
            ), mock.patch.object(
                dispatcher_control, "LOG_PATH", log_path
            ), mock.patch.object(
                dispatcher_control, "_make_daemon_log", return_value=daemon_log
            ):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    self.assertEqual(dispatcher_control.run_once(), 0)
                    self.assertEqual(dispatcher_control.show_workers(as_json=True, task_id="CENTRAL-1", limit=3, recent_hours=12.0), 0)
                    self.assertEqual(dispatcher_control.show_logs(follow=False), 0)
                rendered = stdout.getvalue()
                self.assertIn("runtime ok", rendered)
                self.assertIn("COLOR:line1", rendered)

            with mock.patch.object(dispatcher_control, "ensure_runtime"), mock.patch.object(
                dispatcher_control, "LOG_PATH", log_path
            ), mock.patch.object(
                dispatcher_control, "stream_colored_logs", return_value=130
            ):
                self.assertEqual(dispatcher_control.show_logs(follow=True), 130)

            missing = Path(tmpdir) / "missing.log"
            self.assertIn("no log yet", dispatcher_control.tail_file(missing))

            fake_stdout = io.StringIO("a\nb\n")
            fake_proc = mock.Mock(stdout=fake_stdout)
            fake_proc.wait.return_value = 0
            with mock.patch("dispatcher_control.subprocess.Popen", return_value=fake_proc), mock.patch.object(
                dispatcher_control, "_make_daemon_log", return_value=daemon_log
            ):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    self.assertEqual(dispatcher_control.stream_colored_logs(log_path, lines=2), 0)
                self.assertIn("COLOR:a", stdout.getvalue())

            interrupt_proc = mock.Mock(stdout=iter(["line\n"]))
            interrupt_proc.wait.return_value = 130
            with mock.patch("dispatcher_control.subprocess.Popen", return_value=interrupt_proc), mock.patch.object(
                dispatcher_control, "_make_daemon_log", return_value=mock.Mock(colorize_log_line=mock.Mock(side_effect=KeyboardInterrupt))
            ):
                self.assertEqual(dispatcher_control.stream_colored_logs(log_path, lines=2), 130)
                interrupt_proc.terminate.assert_called_once()
                interrupt_proc.wait.assert_called_with(timeout=5)

    def test_run_check_and_prompt_helpers(self) -> None:
        with mock.patch.object(dispatcher_control, "validate_runtime_importable"), mock.patch.object(
            dispatcher_control, "saved_worker_mode", return_value="stub"
        ), mock.patch.object(
            dispatcher_control, "resolve_worker_model",
            return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default"),
        ), mock.patch.object(
            dispatcher_control, "resolve_effort", return_value=("medium", "default")
        ), mock.patch.object(
            dispatcher_control, "resolve_max_workers",
            return_value=dispatcher_control.ResolvedMaxWorkers(2, "saved_config"),
        ), mock.patch.object(
            dispatcher_control, "_run_self_check_stub", return_value={"planner_status": "done", "runtime_status": "done"}
        ):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(dispatcher_control.run_check(), 0)
            self.assertIn("Dispatcher check passed", stdout.getvalue())

        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="fail")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout="{oops", stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='["bad"]', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='{"planner_status":"todo","runtime_status":"done"}', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='{"planner_status":"done","runtime_status":"failed"}', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()
        with mock.patch("dispatcher_control.subprocess.run", return_value=mock.Mock(returncode=0, stdout='{"planner_status":"done","runtime_status":"done","last_runtime_error":"bad"}', stderr="")):
            with self.assertRaises(SystemExit):
                dispatcher_control._run_self_check_stub()

        with mock.patch("builtins.input", side_effect=["", "bad", "2", "n"]):
            self.assertEqual(dispatcher_control.prompt_with_default("Label", "x"), "x")
            self.assertEqual(dispatcher_control.prompt_positive_int("Workers", 1), 2)
            self.assertFalse(dispatcher_control.prompt_yes_no("Notify", True))
        with mock.patch("builtins.input", side_effect=[""]):
            self.assertEqual(dispatcher_control.prompt_worker_mode("stub"), "stub")
        with mock.patch("builtins.input", side_effect=["4", "3"]):
            self.assertEqual(dispatcher_control.prompt_worker_mode("codex"), "stub")

        with mock.patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(dispatcher_control.MenuExitRequested):
                dispatcher_control.prompt_line(">")

    def test_interactive_helpers_and_main_dispatch(self) -> None:
        with mock.patch("builtins.input", side_effect=["n", "", "5", "abc"]):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(dispatcher_control.run_workers_prompt(), 1)
            self.assertIn("Invalid recent hours", stdout.getvalue())

        with mock.patch("builtins.input", side_effect=["", "because", "n"]):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(dispatcher_control.run_kill_task_prompt(), 1)
            self.assertIn("Task id is required.", stdout.getvalue())

        with mock.patch("builtins.input", side_effect=["CENTRAL-1", "", "y"]), mock.patch.object(
            dispatcher_control, "kill_task", return_value=0
        ) as kill_task:
            self.assertEqual(dispatcher_control.run_kill_task_prompt(), 0)
            kill_task.assert_called_once_with(task_id="CENTRAL-1", reason="operator kill requested", as_json=True)

        with mock.patch.object(dispatcher_control, "resolve_max_workers", return_value=dispatcher_control.ResolvedMaxWorkers(2, "default")), mock.patch.object(
            dispatcher_control, "resolve_worker_model", return_value=dispatcher_control.ResolvedWorkerModel("gpt-5.3-codex", "default")
        ), mock.patch.object(
            dispatcher_control, "saved_worker_mode", return_value="stub"
        ), mock.patch.object(
            dispatcher_control, "saved_notify", return_value=True
        ), mock.patch.object(
            dispatcher_control, "save_config"
        ), mock.patch(
            "builtins.input", side_effect=["3", "", "1", "", "", "", "0"]
        ), mock.patch.object(
            dispatcher_control, "start_dispatcher", return_value=0
        ) as start_dispatcher:
            self.assertEqual(dispatcher_control.run_menu(), 0)
            start_dispatcher.assert_called_once_with(
                restart=True,
                max_workers=2,
                worker_model="gpt-5.3-codex",
                worker_mode="codex",
                notify=True,
            )

        with mock.patch.object(dispatcher_control, "build_parser") as build_parser:
            parser = mock.Mock()
            parser.parse_args.return_value = mock.Mock(command="mystery")
            build_parser.return_value = parser
            self.assertEqual(dispatcher_control.main(["dispatcher_control.py", "mystery"]), 1)
            parser.print_help.assert_called_once()

        with mock.patch.object(dispatcher_control, "build_parser") as build_parser, mock.patch.object(
            dispatcher_control, "show_logs", return_value=0
        ) as show_logs, mock.patch.object(
            dispatcher_control, "show_repo_config", return_value=0
        ) as show_repo_config:
            parser = mock.Mock()
            parser.parse_args.side_effect = [
                mock.Mock(command="follow"),
                mock.Mock(command="repo-config", repo="CENTRAL", max_workers=4, json=True),
            ]
            build_parser.return_value = parser
            self.assertEqual(dispatcher_control.main(["dispatcher_control.py", "follow"]), 0)
            self.assertEqual(dispatcher_control.main(["dispatcher_control.py", "repo-config"]), 0)
            show_logs.assert_called_once_with(follow=True)
            show_repo_config.assert_called_once_with(repo="CENTRAL", max_workers=4, as_json=True)


if __name__ == "__main__":
    unittest.main()
