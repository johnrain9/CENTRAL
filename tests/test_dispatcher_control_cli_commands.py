#!/usr/bin/env python3
"""Subprocess coverage for dispatcher_control.py CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_CONTROL = REPO_ROOT / "scripts" / "dispatcher_control.py"


class DispatcherControlCliCommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="dispatcher_control_cli_")
        tmp_path = Path(self.tmpdir.name)
        self.env = os.environ.copy()
        self.env["CENTRAL_TASK_DB_PATH"] = str(tmp_path / "central_tasks.db")
        self.env["CENTRAL_RUNTIME_STATE_DIR"] = str(tmp_path / "state")
        self.env["CENTRAL_WORKER_MODE"] = "stub"

    def tearDown(self) -> None:
        try:
            self.run_cli("stop", check=False, timeout=20.0)
        finally:
            self.tmpdir.cleanup()

    def run_cli(
        self,
        *args: str,
        check: bool = True,
        timeout: float = 30.0,
        cwd: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DISPATCHER_CONTROL), *args],
            cwd=str(cwd or REPO_ROOT),
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            self.fail(f"dispatcher {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def test_help_output_lists_operator_commands(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("CENTRAL dispatcher operator wrapper", result.stdout)
        self.assertIn("start", result.stdout)
        self.assertIn("restart", result.stdout)
        self.assertIn("kill-task", result.stdout)
        self.assertIn("repo-config", result.stdout)

    def test_start_status_restart_and_stop_commands_work_end_to_end(self) -> None:
        start = self.run_cli(
            "start",
            "--max-workers",
            "1",
            "--worker-model",
            "gpt-5.3-codex",
            "--worker-mode",
            "stub",
        )
        self.assertIn("Dispatcher started", start.stdout)
        self.assertIn("Worker mode: stub", start.stdout)
        self.assertIn("Max workers: 1", start.stdout)

        status = self.run_cli("status")
        status_payload = json.loads(status.stdout)
        self.assertTrue(status_payload["running"])
        self.assertEqual(status_payload["configured_max_workers"], 1)
        self.assertEqual(status_payload["worker_mode"], "stub")

        already_running = self.run_cli(
            "start",
            "--max-workers",
            "3",
            "--worker-model",
            "gpt-5.3-codex",
        )
        self.assertIn("Dispatcher already running; restart is required to apply a new max worker limit.", already_running.stdout)
        self.assertIn("Dispatcher already running; restart is required to apply a new default model.", already_running.stdout)
        self.assertIn('"running": true', already_running.stdout)

        restart = self.run_cli(
            "restart",
            "--max-workers",
            "2",
            "--worker-model",
            "gpt-5.3-codex",
            "--worker-mode",
            "stub",
        )
        self.assertIn("Dispatcher started", restart.stdout)
        self.assertIn("Max workers: 2", restart.stdout)

        restarted_status = self.run_cli("status")
        restarted_payload = json.loads(restarted_status.stdout)
        self.assertEqual(restarted_payload["configured_max_workers"], 2)
        self.assertEqual(restarted_payload["next_restart_max_workers"], 2)

        stop = self.run_cli("stop")
        self.assertEqual(stop.stdout.strip(), "Dispatcher stopped")

    def test_config_command_persists_defaults_used_by_later_start(self) -> None:
        config = self.run_cli(
            "config",
            "--max-workers",
            "2",
            "--worker-model",
            "gpt-5.3-codex",
            "--worker-mode",
            "stub",
            "--notify",
        )
        payload = json.loads(config.stdout)
        self.assertEqual(payload["saved_max_workers"], 2)
        self.assertEqual(payload["saved_default_worker_model"], "gpt-5.3-codex")
        self.assertEqual(payload["saved_worker_mode"], "stub")
        self.assertEqual(payload["saved_notify"], True)

        start = self.run_cli("start")
        self.assertIn("Max workers: 2", start.stdout)
        self.assertIn("Default model: gpt-5.3-codex", start.stdout)
        self.assertIn("Worker mode: stub", start.stdout)

    def test_menu_subcommand_exits_cleanly_from_non_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_control_menu_cwd_") as other_tmp:
            result = self.run_cli("menu", cwd=Path(other_tmp), input_text="0\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Dispatcher Menu", result.stdout)
        self.assertIn("Exiting dispatcher menu.", result.stdout)

    def test_argument_validation_errors_surface_from_parser(self) -> None:
        bad_start = self.run_cli("start", "--max-workers", "0", check=False)
        self.assertEqual(bad_start.returncode, 2)
        self.assertIn("value must be >= 1", bad_start.stderr)

        missing_task_id = self.run_cli("kill-task", check=False)
        self.assertEqual(missing_task_id.returncode, 2)
        self.assertIn("task_id", missing_task_id.stderr)


if __name__ == "__main__":
    unittest.main()
