#!/usr/bin/env python3
"""Tests for REMOTE-4: remote worker coordination integration."""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatcher_control
from central_runtime_v2.config import (
    DEFAULT_COORDINATION_PORT,
    DEFAULT_MAX_REMOTE_WORKERS,
    DEFAULT_MAX_REPO_WORKERS,
    DispatcherConfig,
    REMOTE_WORKERS_ENABLED_ENV,
    COORDINATION_PORT_ENV,
    COORDINATION_TOKEN_ENV,
)
from central_runtime_v2.dispatcher import CentralDispatcher


# ---------------------------------------------------------------------------
# Config constant tests
# ---------------------------------------------------------------------------


class RemoteWorkersConfigConstantsTest(unittest.TestCase):
    def test_env_var_names(self) -> None:
        self.assertEqual(REMOTE_WORKERS_ENABLED_ENV, "CENTRAL_REMOTE_WORKERS")
        self.assertEqual(COORDINATION_PORT_ENV, "CENTRAL_COORDINATION_PORT")
        self.assertEqual(COORDINATION_TOKEN_ENV, "CENTRAL_COORDINATION_TOKEN")

    def test_default_values(self) -> None:
        self.assertEqual(DEFAULT_COORDINATION_PORT, 7429)
        self.assertEqual(DEFAULT_MAX_REMOTE_WORKERS, 3)
        self.assertEqual(DEFAULT_MAX_REPO_WORKERS, 3)


# ---------------------------------------------------------------------------
# DispatcherConfig remote fields test
# ---------------------------------------------------------------------------


class DispatcherConfigRemoteFieldsTest(unittest.TestCase):
    def _make_config(self, **kwargs) -> DispatcherConfig:
        base = dict(
            db_path=Path("/tmp/test.db"),
            state_dir=Path("/tmp/state"),
            max_workers=2,
            poll_interval=1.0,
            heartbeat_seconds=5.0,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model="gpt-5.3-codex",
        )
        base.update(kwargs)
        return DispatcherConfig(**base)

    def test_default_remote_fields(self) -> None:
        cfg = self._make_config()
        self.assertFalse(cfg.remote_workers_enabled)
        self.assertEqual(cfg.coordination_port, DEFAULT_COORDINATION_PORT)
        self.assertEqual(cfg.max_remote_workers, DEFAULT_MAX_REMOTE_WORKERS)
        self.assertEqual(cfg.max_repo_workers, DEFAULT_MAX_REPO_WORKERS)

    def test_custom_remote_fields(self) -> None:
        cfg = self._make_config(
            remote_workers_enabled=True,
            coordination_port=8000,
            max_remote_workers=5,
            max_repo_workers=2,
        )
        self.assertTrue(cfg.remote_workers_enabled)
        self.assertEqual(cfg.coordination_port, 8000)
        self.assertEqual(cfg.max_remote_workers, 5)
        self.assertEqual(cfg.max_repo_workers, 2)


# ---------------------------------------------------------------------------
# dispatcher_control save/load config round-trip
# ---------------------------------------------------------------------------


class DispatcherControlRemoteConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="remote_config_test_")
        self.state_dir = Path(self.tmpdir.name)
        self._orig_state_dir = dispatcher_control.STATE_DIR
        self._orig_config_path = dispatcher_control.CONFIG_PATH
        dispatcher_control.STATE_DIR = self.state_dir
        dispatcher_control.CONFIG_PATH = self.state_dir / "dispatcher-config.json"

    def tearDown(self) -> None:
        dispatcher_control.STATE_DIR = self._orig_state_dir
        dispatcher_control.CONFIG_PATH = self._orig_config_path
        self.tmpdir.cleanup()

    def test_save_and_load_remote_config(self) -> None:
        dispatcher_control.save_config(
            remote_workers_enabled=True,
            coordination_port=9000,
            max_remote_workers=4,
            max_repo_workers=2,
        )
        payload = dispatcher_control.load_saved_config()
        self.assertTrue(payload["remote_workers_enabled"])
        self.assertEqual(payload["coordination_port"], 9000)
        self.assertEqual(payload["max_remote_workers"], 4)
        self.assertEqual(payload["max_repo_workers"], 2)

    def test_load_saved_config_backward_compat_defaults(self) -> None:
        """Existing config without remote fields gets defaults on load."""
        # Write a legacy config without remote fields
        legacy = {"worker_mode": "claude", "max_workers": 2}
        dispatcher_control.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        dispatcher_control.CONFIG_PATH.write_text(json.dumps(legacy), encoding="utf-8")

        payload = dispatcher_control.load_saved_config()
        self.assertFalse(payload["remote_workers_enabled"])
        self.assertEqual(payload["coordination_port"], DEFAULT_COORDINATION_PORT)
        self.assertEqual(payload["max_remote_workers"], DEFAULT_MAX_REMOTE_WORKERS)
        self.assertEqual(payload["max_repo_workers"], DEFAULT_MAX_REPO_WORKERS)

    def test_saved_helpers_return_correct_values(self) -> None:
        dispatcher_control.save_config(
            remote_workers_enabled=True,
            coordination_port=7429,
            max_remote_workers=3,
            max_repo_workers=3,
        )
        self.assertTrue(dispatcher_control.saved_remote_workers_enabled())
        self.assertEqual(dispatcher_control.saved_coordination_port(), 7429)
        self.assertEqual(dispatcher_control.saved_max_remote_workers(), 3)
        self.assertEqual(dispatcher_control.saved_max_repo_workers(), 3)


# ---------------------------------------------------------------------------
# _SAFE_SHELL_KEYS includes CENTRAL_COORDINATION_TOKEN
# ---------------------------------------------------------------------------


class SafeShellKeysTest(unittest.TestCase):
    def test_coordination_token_in_safe_keys(self) -> None:
        self.assertIn("CENTRAL_COORDINATION_TOKEN", dispatcher_control._SAFE_SHELL_KEYS)


# ---------------------------------------------------------------------------
# Argparse includes new remote-worker flags on config subcommand
# ---------------------------------------------------------------------------


class RemoteWorkersArgparseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = dispatcher_control.build_parser()

    def test_config_subparser_has_remote_workers_flag(self) -> None:
        args = self.parser.parse_args(["config", "--remote-workers"])
        self.assertTrue(args.remote_workers_enabled)

    def test_config_subparser_has_no_remote_workers_flag(self) -> None:
        args = self.parser.parse_args(["config", "--no-remote-workers"])
        self.assertFalse(args.remote_workers_enabled)

    def test_config_subparser_has_coordination_port(self) -> None:
        args = self.parser.parse_args(["config", "--coordination-port", "8080"])
        self.assertEqual(args.coordination_port, 8080)

    def test_config_subparser_has_max_remote_workers(self) -> None:
        args = self.parser.parse_args(["config", "--max-remote-workers", "5"])
        self.assertEqual(args.max_remote_workers, 5)

    def test_config_subparser_has_max_repo_workers(self) -> None:
        args = self.parser.parse_args(["config", "--max-repo-workers", "2"])
        self.assertEqual(args.max_repo_workers, 2)


# ---------------------------------------------------------------------------
# CentralDispatcher bridge protocol
# ---------------------------------------------------------------------------


class DispatcherBridgeProtocolTest(unittest.TestCase):
    def _make_config(self) -> DispatcherConfig:
        return DispatcherConfig(
            db_path=Path("/tmp/test.db"),
            state_dir=Path("/tmp/state"),
            max_workers=2,
            poll_interval=1.0,
            heartbeat_seconds=5.0,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model="gpt-5.3-codex",
        )

    def test_db_path_property(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        self.assertEqual(dispatcher.db_path, Path("/tmp/test.db"))

    def test_dispatcher_config_property(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        self.assertIs(dispatcher.dispatcher_config, cfg)

    def test_active_workers_property(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        self.assertIs(dispatcher.active_workers, dispatcher._active)

    def test_active_lock_property(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        self.assertIs(dispatcher.active_lock, dispatcher._active_lock)

    def test_dispatcher_version_returns_string(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        ver = dispatcher.dispatcher_version()
        self.assertIsInstance(ver, str)
        self.assertGreater(len(ver), 0)

    def test_dispatcher_id_returns_string(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        did = dispatcher.dispatcher_id()
        self.assertIsInstance(did, str)
        self.assertGreater(len(did), 0)

    def test_started_at_returns_float(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        sat = dispatcher.started_at()
        self.assertIsInstance(sat, float)
        # Should be recent (within last minute)
        self.assertGreater(sat, time.time() - 60)

    def test_coordination_server_is_none_by_default(self) -> None:
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        self.assertIsNone(dispatcher._coordination_server)


# ---------------------------------------------------------------------------
# _process_active drains finalization queue for remote workers
# ---------------------------------------------------------------------------


class ProcessActiveFinalizationQueueTest(unittest.TestCase):
    def _make_config(self) -> DispatcherConfig:
        return DispatcherConfig(
            db_path=Path("/tmp/test.db"),
            state_dir=Path("/tmp/state"),
            max_workers=2,
            poll_interval=1.0,
            heartbeat_seconds=30.0,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model="gpt-5.3-codex",
        )

    def test_drains_finalization_queue_and_finalizes(self) -> None:
        """Results submitted via HTTP end up finalized through the main loop."""
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)

        # Create a mock coordination server with a finalization queue
        mock_server = mock.Mock()
        fq = queue.Queue()
        mock_server.finalization_queue = fq
        dispatcher._coordination_server = mock_server

        # Create a mock remote ActiveWorker
        from central_runtime_v2.config import ActiveWorker
        from datetime import datetime, timezone

        mock_task = {"task_id": "ECO-999", "title": "test", "target_repo_root": "/tmp"}
        state = ActiveWorker(
            task=mock_task,
            worker_id="remote:wsl2:123",
            run_id="ECO-999-123",
            pid=-1,
            proc=None,
            log_handle=None,
            prompt_path=Path("/tmp/prompt.md"),
            result_path=Path("/tmp/result.json"),
            log_path=Path("/tmp/log.log"),
            process_start_token=None,
            started_at=datetime.now(timezone.utc),
            start_monotonic=time.monotonic(),
            last_heartbeat_monotonic=time.monotonic(),
            timeout_seconds=3600,
            is_remote=True,
            remote_worker_id="wsl2",
        )
        dispatcher._active["ECO-999"] = state

        # Enqueue a finalization
        fq.put(("ECO-999", "ECO-999-123"))

        finalized = []
        def fake_finalize(s, timed_out):
            finalized.append((s.run_id, timed_out))

        with mock.patch.object(dispatcher, "_finalize_worker", side_effect=fake_finalize), \
             mock.patch.object(dispatcher, "_close_worker_state"), \
             mock.patch.object(dispatcher, "_emit_status_heartbeat"):
            dispatcher._process_active()

        self.assertEqual(finalized, [("ECO-999-123", False)])
        self.assertNotIn("ECO-999", dispatcher._active)

    def test_no_coordination_server_does_not_error(self) -> None:
        """When remote workers are disabled, _process_active works normally."""
        cfg = self._make_config()
        dispatcher = CentralDispatcher(cfg)
        # No coordination server set — should not raise
        self.assertIsNone(dispatcher._coordination_server)
        # _process_active with no active workers and no server should be a no-op
        dispatcher._process_active()


# ---------------------------------------------------------------------------
# _process_active handles remote worker heartbeat liveness timeout
# ---------------------------------------------------------------------------


class RemoteWorkerHeartbeatTimeoutTest(unittest.TestCase):
    def _make_config(self, heartbeat_seconds: float = 30.0) -> DispatcherConfig:
        return DispatcherConfig(
            db_path=Path("/tmp/test.db"),
            state_dir=Path("/tmp/state"),
            max_workers=2,
            poll_interval=1.0,
            heartbeat_seconds=heartbeat_seconds,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model="gpt-5.3-codex",
        )

    def _make_remote_state(self, last_heartbeat_offset: float = 0.0) -> "ActiveWorker":
        from central_runtime_v2.config import ActiveWorker
        from datetime import datetime, timezone

        mock_task = {"task_id": "ECO-1", "title": "test", "target_repo_root": "/tmp"}
        return ActiveWorker(
            task=mock_task,
            worker_id="remote:wsl2:123",
            run_id="ECO-1-123",
            pid=-1,
            proc=None,
            log_handle=None,
            prompt_path=Path("/tmp/prompt.md"),
            result_path=Path("/tmp/result.json"),
            log_path=Path("/tmp/log.log"),
            process_start_token=None,
            started_at=datetime.now(timezone.utc),
            start_monotonic=time.monotonic() - 10,  # started 10s ago
            last_heartbeat_monotonic=time.monotonic() - last_heartbeat_offset,
            timeout_seconds=3600,
            is_remote=True,
            remote_worker_id="wsl2",
        )

    def test_remote_worker_stale_heartbeat_triggers_timeout(self) -> None:
        """A remote worker whose heartbeat is older than liveness window is timed out."""
        cfg = self._make_config(heartbeat_seconds=10.0)
        dispatcher = CentralDispatcher(cfg)

        # liveness_window = 10s * 3 = 30s. Make heartbeat 60s stale.
        state = self._make_remote_state(last_heartbeat_offset=60.0)
        dispatcher._active["ECO-1"] = state

        finalized = []
        def fake_finalize(s, timed_out):
            finalized.append((s.run_id, timed_out))

        with mock.patch.object(dispatcher, "_finalize_worker", side_effect=fake_finalize), \
             mock.patch.object(dispatcher, "_close_worker_state"), \
             mock.patch.object(dispatcher, "_emit_status_heartbeat"):
            dispatcher._process_active()

        self.assertEqual(finalized, [("ECO-1-123", True)])
        self.assertNotIn("ECO-1", dispatcher._active)

    def test_remote_worker_fresh_heartbeat_not_timed_out(self) -> None:
        """A remote worker with a recent heartbeat is not timed out."""
        cfg = self._make_config(heartbeat_seconds=30.0)
        dispatcher = CentralDispatcher(cfg)

        # liveness_window = 30s * 3 = 90s. Make heartbeat 5s old — within window.
        state = self._make_remote_state(last_heartbeat_offset=5.0)
        dispatcher._active["ECO-1"] = state

        finalized = []
        with mock.patch.object(dispatcher, "_finalize_worker", side_effect=lambda s, **kw: finalized.append(s.run_id)), \
             mock.patch.object(dispatcher, "_close_worker_state"), \
             mock.patch.object(dispatcher, "_emit_status_heartbeat"):
            dispatcher._process_active()

        self.assertEqual(finalized, [])
        self.assertIn("ECO-1", dispatcher._active)


# ---------------------------------------------------------------------------
# commands.py build_dispatcher_config passes remote fields
# ---------------------------------------------------------------------------


class BuildDispatcherConfigRemoteFieldsTest(unittest.TestCase):
    def test_remote_fields_passed_to_config(self) -> None:
        from central_runtime_v2.commands import build_dispatcher_config
        import argparse

        args = argparse.Namespace(
            db_path=None,
            state_dir=None,
            max_workers=1,
            poll_interval=1.0,
            heartbeat_seconds=5.0,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model=None,
            default_codex_model=None,
            notify=False,
            audit_worker_model=None,
            remote_workers=True,
            coordination_port=9000,
            max_remote_workers=5,
            max_repo_workers=2,
        )

        cfg = build_dispatcher_config(args)
        self.assertTrue(cfg.remote_workers_enabled)
        self.assertEqual(cfg.coordination_port, 9000)
        self.assertEqual(cfg.max_remote_workers, 5)
        self.assertEqual(cfg.max_repo_workers, 2)

    def test_remote_fields_default_when_disabled(self) -> None:
        from central_runtime_v2.commands import build_dispatcher_config
        import argparse

        args = argparse.Namespace(
            db_path=None,
            state_dir=None,
            max_workers=1,
            poll_interval=1.0,
            heartbeat_seconds=5.0,
            status_heartbeat_seconds=30.0,
            stale_recovery_seconds=10.0,
            worker_mode="stub",
            default_worker_model=None,
            default_codex_model=None,
            notify=False,
            audit_worker_model=None,
            remote_workers=False,
            coordination_port=DEFAULT_COORDINATION_PORT,
            max_remote_workers=DEFAULT_MAX_REMOTE_WORKERS,
            max_repo_workers=DEFAULT_MAX_REPO_WORKERS,
        )

        cfg = build_dispatcher_config(args)
        self.assertFalse(cfg.remote_workers_enabled)
        self.assertEqual(cfg.coordination_port, DEFAULT_COORDINATION_PORT)


# ---------------------------------------------------------------------------
# launcher_status_payload includes remote worker fields
# ---------------------------------------------------------------------------


class LauncherStatusPayloadRemoteFieldsTest(unittest.TestCase):
    def test_status_includes_remote_fields(self) -> None:
        mock_runtime_payload = {
            "configured_max_workers": 1,
            "worker_mode": "stub",
        }
        with mock.patch.object(dispatcher_control, "runtime_status_payload", return_value=mock_runtime_payload), \
             mock.patch.object(dispatcher_control, "saved_remote_workers_enabled", return_value=True), \
             mock.patch.object(dispatcher_control, "saved_coordination_port", return_value=7429), \
             mock.patch.object(dispatcher_control, "saved_max_remote_workers", return_value=3), \
             mock.patch.object(dispatcher_control, "saved_max_repo_workers", return_value=3):
            payload = dispatcher_control.launcher_status_payload()

        self.assertIn("remote_workers_enabled", payload)
        self.assertIn("coordination_port", payload)
        self.assertIn("max_remote_workers", payload)
        self.assertIn("max_repo_workers", payload)
        self.assertIn("coordination_api_url", payload)
        self.assertTrue(payload["remote_workers_enabled"])
        self.assertEqual(payload["coordination_api_url"], "http://0.0.0.0:7429")

    def test_coordination_api_url_none_when_disabled(self) -> None:
        mock_runtime_payload = {}
        with mock.patch.object(dispatcher_control, "runtime_status_payload", return_value=mock_runtime_payload), \
             mock.patch.object(dispatcher_control, "saved_remote_workers_enabled", return_value=False), \
             mock.patch.object(dispatcher_control, "saved_coordination_port", return_value=7429), \
             mock.patch.object(dispatcher_control, "saved_max_remote_workers", return_value=3), \
             mock.patch.object(dispatcher_control, "saved_max_repo_workers", return_value=3):
            payload = dispatcher_control.launcher_status_payload()

        self.assertIsNone(payload["coordination_api_url"])


if __name__ == "__main__":
    unittest.main()
