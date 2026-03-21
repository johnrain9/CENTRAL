#!/usr/bin/env python3
"""Tests for interactive dispatcher menu surfaces."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_CONTROL = REPO_ROOT / "scripts" / "dispatcher_control.py"
DISPATCHER_MENU = REPO_ROOT / "scripts" / "dispatcher_menu.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dispatcher_control


class DispatcherMenuFlowTest(unittest.TestCase):
    def test_menu_start_uses_prompted_dispatcher_args(self) -> None:
        inputs = iter([
            "1",  # start
            "4",  # max workers
            "gpt-5.3-codex",  # worker model
            "2",  # claude mode
            "y",  # notify
            "0",  # exit
        ])

        with mock.patch.object(dispatcher_control, "ensure_runtime"), mock.patch.object(
            dispatcher_control, "init_db"
        ), mock.patch.object(
            dispatcher_control, "running_pid", return_value=None
        ), mock.patch.object(
            dispatcher_control, "resolve_max_workers", return_value=dispatcher_control.ResolvedMaxWorkers(value=2, source="default")
        ), mock.patch.object(
            dispatcher_control, "resolve_worker_model", return_value=dispatcher_control.ResolvedWorkerModel(value="gpt-5.3-codex", source="default")
        ), mock.patch.object(
            dispatcher_control, "saved_worker_mode", return_value="codex"
        ), mock.patch.object(
            dispatcher_control, "saved_notify", return_value=False
        ), mock.patch(
            "builtins.input", side_effect=lambda _: next(inputs)
        ), mock.patch.object(
            dispatcher_control, "start_dispatcher", return_value=0
        ) as start_dispatcher:
            rc = dispatcher_control.run_menu()

        self.assertEqual(rc, 0)
        start_dispatcher.assert_called_once_with(
            restart=False,
            max_workers=4,
            worker_model="gpt-5.3-codex",
            worker_mode="claude",
            notify=True,
        )


class DispatcherMenuAnywhereTest(unittest.TestCase):
    def test_symlinked_menu_command_runs_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_menu_anywhere_") as tmpdir:
            tmp_path = Path(tmpdir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "dispatcher-menu"
            launcher.symlink_to(DISPATCHER_MENU)

            env = os.environ.copy()
            env["CENTRAL_TASK_DB_PATH"] = str(tmp_path / "central_tasks.db")
            env["CENTRAL_RUNTIME_STATE_DIR"] = str(tmp_path / "state")

            result = subprocess.run(
                [str(launcher)],
                cwd=str(tmp_path),
                input="0\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=30.0,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("Dispatcher Menu", result.stdout)

    def test_menu_subcommand_runs_from_non_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dispatcher_menu_cwd_") as tmpdir:
            tmp_path = Path(tmpdir)
            env = os.environ.copy()
            env["CENTRAL_TASK_DB_PATH"] = str(tmp_path / "central_tasks.db")
            env["CENTRAL_RUNTIME_STATE_DIR"] = str(tmp_path / "state")

            result = subprocess.run(
                [sys.executable, str(DISPATCHER_CONTROL), "menu"],
                cwd=str(tmp_path),
                input="0\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=30.0,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("Dispatcher Menu", result.stdout)


if __name__ == "__main__":
    unittest.main()
