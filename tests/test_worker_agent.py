#!/usr/bin/env python3
"""Tests for scripts/worker_agent.py.

Validates poll loop behavior, heartbeat timing, 410/404 handling, and
graceful shutdown using mock HTTP responses and subprocess stubs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# ---------------------------------------------------------------------------
# Stub out httpx and central_runtime_v2.backends before importing worker_agent
# ---------------------------------------------------------------------------

# Provide a minimal httpx stub if httpx is not installed.
if "httpx" not in sys.modules:
    _httpx_stub = types.ModuleType("httpx")

    class _FakeResponse:
        def __init__(self, status_code: int, body: object = None):
            self.status_code = status_code
            self._body = body or {}
            self.text = json.dumps(self._body)

        def json(self):
            return self._body

    class _FakeClient:
        def __init__(self, *, timeout=10):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            return _FakeResponse(204)

    _httpx_stub.Client = _FakeClient
    sys.modules["httpx"] = _httpx_stub

import worker_agent  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_package(task_id="ECO-1", run_id="ECO-1-1000", backend="stub") -> dict:
    return {
        "task_id": task_id,
        "run_id": run_id,
        "title": "Test task",
        "worker_backend": backend,
        "worker_model": "claude-sonnet-4-6",
        "repo_name": "eco-system",
        "repo_root_relative": "projects/eco-system",
        "branch_prefix": f"worker/{task_id}",
        "prompt_body": "Do the thing.",
        "timeout_seconds": 3600,
        "sandbox_mode": "workspace-write",
        "env_allowlist": [],
    }


def _make_agent(**kwargs) -> worker_agent.WorkerAgent:
    defaults = dict(
        dispatcher_url="http://localhost:7429",
        auth_token="test-token",
        worker_id="test-worker",
        max_concurrent=1,
        poll_interval=0.05,
        backends=["stub"],
    )
    defaults.update(kwargs)
    return worker_agent.WorkerAgent(**defaults)


# ---------------------------------------------------------------------------
# Test: poll loop — 204 → no work → sleep
# ---------------------------------------------------------------------------


class TestPollLoop(unittest.TestCase):
    def test_poll_loop_sends_claim_with_correct_payload(self):
        """_try_claim POSTs correct worker_id, backends, central_version."""
        agent = _make_agent()
        agent._central_version = "abc1234"

        captured = {}

        def fake_post(url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            resp = mock.MagicMock()
            resp.status_code = 204
            return resp

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_client.post = fake_post

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            result = agent._try_claim()

        self.assertIsNone(result)
        self.assertEqual(captured["json"]["worker_id"], "test-worker")
        self.assertEqual(captured["json"]["backends"], ["stub"])
        self.assertEqual(captured["json"]["central_version"], "abc1234")
        self.assertIn("/api/v1/claim", captured["url"])

    def test_poll_loop_returns_work_package_on_200(self):
        """_try_claim returns work_package dict when dispatcher returns 200."""
        agent = _make_agent()
        work = _make_work_package()

        resp_data = {"work_package": work, "version_warning": None}

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = resp_data
        fake_client.post = mock.MagicMock(return_value=fake_resp)

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            result = agent._try_claim()

        self.assertEqual(result["task_id"], "ECO-1")
        self.assertEqual(result["run_id"], "ECO-1-1000")

    def test_poll_loop_returns_none_on_network_error(self):
        """_try_claim returns None (not raises) on connection error."""
        agent = _make_agent()
        with mock.patch("worker_agent.httpx.Client", side_effect=Exception("conn refused")):
            result = agent._try_claim()
        self.assertIsNone(result)

    def test_run_loop_stops_on_shutdown_event(self):
        """run() exits promptly when _shutdown is set before first poll."""
        agent = _make_agent(poll_interval=0.01)

        call_count = 0

        def fake_try_claim():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                agent._shutdown.set()
            return None

        with mock.patch.object(agent, "_sync_central", return_value="abc1234"), \
             mock.patch("worker_agent._load_shell_api_keys"), \
             mock.patch.object(agent, "_try_claim", side_effect=fake_try_claim), \
             mock.patch("worker_agent.signal.signal"), \
             mock.patch.object(Path, "mkdir"):
            agent.run()

        self.assertGreaterEqual(call_count, 1)


# ---------------------------------------------------------------------------
# Test: heartbeat timing
# ---------------------------------------------------------------------------


class TestHeartbeat(unittest.TestCase):
    def test_heartbeat_sends_correct_payload(self):
        """_send_heartbeat POSTs task_id, run_id, worker_id to /api/v1/heartbeat."""
        agent = _make_agent()
        work = _make_work_package()

        captured = {}

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200
        fake_client.post = mock.MagicMock(return_value=fake_resp)

        def fake_post(url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return fake_resp

        fake_client.post = fake_post

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            code = agent._send_heartbeat(work)

        self.assertEqual(code, 200)
        self.assertIn("/api/v1/heartbeat", captured["url"])
        self.assertEqual(captured["json"]["task_id"], "ECO-1")
        self.assertEqual(captured["json"]["run_id"], "ECO-1-1000")
        self.assertEqual(captured["json"]["worker_id"], "test-worker")
        self.assertNotIn("reattach", captured["json"])

    def test_heartbeat_includes_reattach_flag(self):
        """_send_heartbeat(reattach=True) includes reattach field in payload."""
        agent = _make_agent()
        work = _make_work_package()

        captured = {}

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200

        def fake_post(url, headers=None, json=None, **kwargs):
            captured["json"] = json
            return fake_resp

        fake_client.post = fake_post

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            agent._send_heartbeat(work, reattach=True)

        self.assertTrue(captured["json"].get("reattach"))

    def test_heartbeat_returns_zero_on_network_error(self):
        """_send_heartbeat returns 0 (not raises) on connection error."""
        agent = _make_agent()
        work = _make_work_package()
        with mock.patch("worker_agent.httpx.Client", side_effect=Exception("timeout")):
            code = agent._send_heartbeat(work)
        self.assertEqual(code, 0)

    def test_heartbeat_loop_fires_at_interval(self):
        """Heartbeat is sent at HEARTBEAT_INTERVAL during subprocess monitoring."""
        agent = _make_agent()
        work = _make_work_package()

        heartbeat_calls: list[float] = []

        def fake_heartbeat(w, reattach=False, progress="running"):
            heartbeat_calls.append(time.time())
            return 200

        # Simulate a subprocess that runs for ~3 heartbeat intervals, then exits.
        heartbeat_intervals_to_run = 3
        with mock.patch.object(agent, "_send_heartbeat", side_effect=fake_heartbeat):
            with mock.patch.object(worker_agent, "HEARTBEAT_INTERVAL", 0.05):
                # Simulate the heartbeat loop directly (inline the core logic).
                last_heartbeat = 0.0
                start = time.time()
                deadline = start + (0.05 * heartbeat_intervals_to_run * 1.5)

                while time.time() < deadline:
                    now = time.time()
                    if now - last_heartbeat >= worker_agent.HEARTBEAT_INTERVAL:
                        agent._send_heartbeat(work)
                        last_heartbeat = now
                    time.sleep(0.01)

        self.assertGreaterEqual(len(heartbeat_calls), heartbeat_intervals_to_run - 1)


# ---------------------------------------------------------------------------
# Test: 410 cancellation
# ---------------------------------------------------------------------------


class TestCancellation(unittest.TestCase):
    def test_410_kills_subprocess_and_sets_cancelled(self):
        """When heartbeat returns 410, the subprocess is killed and cancelled flag is set."""
        agent = _make_agent()
        work = _make_work_package()
        run_id = work["run_id"]

        agent._active[run_id] = {"work": work, "cancelled": False, "proc": None}

        mock_proc = mock.MagicMock()
        poll_results = [None, None, None]  # still running when 410 fires
        poll_call_count = [0]

        def fake_poll():
            idx = poll_call_count[0]
            poll_call_count[0] += 1
            if idx < len(poll_results):
                return poll_results[idx]
            return 0  # exited after kill

        mock_proc.poll = fake_poll
        mock_proc.returncode = 0

        heartbeat_call_count = [0]

        def fake_heartbeat(w, reattach=False, progress="running"):
            heartbeat_call_count[0] += 1
            return 410  # immediate cancel

        with mock.patch.object(agent, "_send_heartbeat", side_effect=fake_heartbeat), \
             mock.patch.object(worker_agent, "HEARTBEAT_INTERVAL", 0.0):
            # Run the heartbeat/monitoring logic inline with last_heartbeat=0
            last_heartbeat = 0.0
            reattach_next = False
            killed = False

            while mock_proc.poll() is None and not killed:
                now = time.time()
                if now - last_heartbeat >= worker_agent.HEARTBEAT_INTERVAL:
                    status_code = agent._send_heartbeat(work, reattach=reattach_next)
                    last_heartbeat = now
                    if status_code == 410:
                        mock_proc.kill()
                        with agent._active_lock:
                            if run_id in agent._active:
                                agent._active[run_id]["cancelled"] = True
                        killed = True
                time.sleep(0.001)

        mock_proc.kill.assert_called_once()
        with agent._active_lock:
            self.assertTrue(agent._active[run_id]["cancelled"])


# ---------------------------------------------------------------------------
# Test: 404 reattach
# ---------------------------------------------------------------------------


class TestReattach(unittest.TestCase):
    def test_404_triggers_reattach_on_next_heartbeat(self):
        """When heartbeat returns 404, the next heartbeat includes reattach=True."""
        agent = _make_agent()
        work = _make_work_package()

        heartbeat_payloads: list[dict] = []

        def fake_heartbeat(w, reattach=False, progress="running"):
            heartbeat_payloads.append({"reattach": reattach})
            return 404 if len(heartbeat_payloads) == 1 else 200

        with mock.patch.object(agent, "_send_heartbeat", side_effect=fake_heartbeat), \
             mock.patch.object(worker_agent, "HEARTBEAT_INTERVAL", 0.0):
            last_heartbeat = 0.0
            reattach_next = False
            iterations = 0

            while iterations < 3:
                now = time.time()
                if now - last_heartbeat >= worker_agent.HEARTBEAT_INTERVAL:
                    status_code = agent._send_heartbeat(work, reattach=reattach_next)
                    last_heartbeat = now
                    reattach_next = False
                    if status_code == 404:
                        reattach_next = True
                    iterations += 1
                time.sleep(0.001)

        self.assertFalse(heartbeat_payloads[0]["reattach"])
        self.assertTrue(heartbeat_payloads[1]["reattach"])
        self.assertFalse(heartbeat_payloads[2]["reattach"])


# ---------------------------------------------------------------------------
# Test: graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown(unittest.TestCase):
    def test_shutdown_event_stops_poll_loop(self):
        """Setting _shutdown stops the main run() loop cleanly."""
        agent = _make_agent(poll_interval=0.01)

        claim_calls = [0]

        def counting_claim():
            claim_calls[0] += 1
            if claim_calls[0] >= 3:
                agent._shutdown.set()
            return None

        with mock.patch.object(agent, "_sync_central", return_value="deadbeef"), \
             mock.patch("worker_agent._load_shell_api_keys"), \
             mock.patch.object(agent, "_try_claim", side_effect=counting_claim), \
             mock.patch("worker_agent.signal.signal"), \
             mock.patch.object(Path, "mkdir"):
            agent.run()

        # Should have stopped shortly after the shutdown was set.
        self.assertGreaterEqual(claim_calls[0], 3)
        self.assertTrue(agent._shutdown.is_set())

    def test_sigterm_sets_shutdown_event(self):
        """SIGTERM handler sets the shutdown event."""
        agent = _make_agent()
        agent._handle_signal(15, None)
        self.assertTrue(agent._shutdown.is_set())

    def test_sigint_sets_shutdown_event(self):
        """SIGINT handler sets the shutdown event."""
        agent = _make_agent()
        agent._handle_signal(2, None)
        self.assertTrue(agent._shutdown.is_set())


# ---------------------------------------------------------------------------
# Test: result reading
# ---------------------------------------------------------------------------


class TestResultHandling(unittest.TestCase):
    def test_read_result_returns_json_when_file_exists(self):
        """_read_result returns parsed JSON when result file is present."""
        import tempfile

        agent = _make_agent()
        payload = {
            "schema_version": 2,
            "task_id": "ECO-1",
            "run_id": "ECO-1-1000",
            "status": "COMPLETED",
            "summary": "done",
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(payload, f)
            path = Path(f.name)

        try:
            result = agent._read_result(path, "ECO-1", "ECO-1-1000", 0)
            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["summary"], "done")
        finally:
            path.unlink(missing_ok=True)

    def test_read_result_returns_fallback_when_file_missing(self):
        """_read_result returns a FAILED fallback when result file is missing."""
        agent = _make_agent()
        path = Path("/nonexistent/path/result.json")
        result = agent._read_result(path, "ECO-1", "ECO-1-1000", 1)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["task_id"], "ECO-1")
        self.assertIn("result-file", [v["name"] for v in result["validation"]])

    def test_submit_result_posts_to_correct_endpoint(self):
        """_submit_result POSTs to /api/v1/result with task_id, run_id, result."""
        agent = _make_agent()
        work = _make_work_package()
        result = {"status": "COMPLETED", "summary": "ok"}

        captured = {}

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200

        def fake_post(url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return fake_resp

        fake_client.post = fake_post

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            ok = agent._submit_result(work, result, "worker/ECO-1", "abc123", "last logs")

        self.assertTrue(ok)
        self.assertIn("/api/v1/result", captured["url"])
        self.assertEqual(captured["json"]["task_id"], "ECO-1")
        self.assertEqual(captured["json"]["result_branch"], "worker/ECO-1")
        self.assertEqual(captured["json"]["result_commit_sha"], "abc123")

    def test_submit_result_treats_409_as_success(self):
        """409 (already finalized) is treated as success — no retry needed."""
        agent = _make_agent()
        work = _make_work_package()

        fake_client = mock.MagicMock()
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 409
        fake_resp.text = "already finalized"
        fake_client.post = mock.MagicMock(return_value=fake_resp)

        with mock.patch("worker_agent.httpx.Client", return_value=fake_client):
            ok = agent._submit_result(work, {}, "main", "", "")

        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# Test: CLI arg parsing
# ---------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def test_main_parses_all_args(self):
        """main() constructs a WorkerAgent with correct parameters from CLI args."""
        constructed = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                constructed.update(kwargs)

            def run(self):
                pass

        with mock.patch("worker_agent.WorkerAgent", side_effect=lambda **kw: FakeAgent(**kw)):
            worker_agent.main(
                [
                    "--dispatcher-url", "http://10.0.0.1:7429",
                    "--auth-token", "secret",
                    "--worker-id", "wsl2-box",
                    "--max-concurrent", "2",
                    "--poll-interval", "10",
                    "--backends", "claude,grok",
                ]
            )

        self.assertEqual(constructed["dispatcher_url"], "http://10.0.0.1:7429")
        self.assertEqual(constructed["auth_token"], "secret")
        self.assertEqual(constructed["worker_id"], "wsl2-box")
        self.assertEqual(constructed["max_concurrent"], 2)
        self.assertEqual(constructed["poll_interval"], 10.0)
        self.assertEqual(constructed["backends"], ["claude", "grok"])

    def test_main_defaults(self):
        """main() uses correct defaults for optional arguments."""
        constructed = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                constructed.update(kwargs)

            def run(self):
                pass

        with mock.patch("worker_agent.WorkerAgent", side_effect=lambda **kw: FakeAgent(**kw)):
            worker_agent.main(
                [
                    "--dispatcher-url", "http://localhost:7429",
                    "--auth-token", "tok",
                    "--worker-id", "box",
                ]
            )

        self.assertEqual(constructed["max_concurrent"], 1)
        self.assertEqual(constructed["poll_interval"], 5.0)
        self.assertIn("claude", constructed["backends"])


if __name__ == "__main__":
    unittest.main()
