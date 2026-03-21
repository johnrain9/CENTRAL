#!/usr/bin/env python3
"""Behavior tests for central_runtime_v2.commands."""

from __future__ import annotations

import argparse
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from central_runtime_v2 import commands


class CommandsBehaviorTest(unittest.TestCase):
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

    def test_smoke_task_payload_contains_required_creation_fields(self) -> None:
        payload = commands.smoke_task_payload()
        self.assertEqual(payload["initiative"], "one-off")
        self.assertIn("metadata", payload)
        self.assertIn("execution", payload)
        self.assertEqual(payload["task_id"], commands.SELF_CHECK_TASK_ID)


if __name__ == "__main__":
    unittest.main()
