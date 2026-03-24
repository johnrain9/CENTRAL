#!/usr/bin/env python3
"""Unit tests for the remote-worker coordination API (coordination.py).

All tests use a MockDispatcherBridge backed by a real SQLite DB so that
task-claim / lease logic goes through the actual central_task_db code paths.
The FastAPI test client (httpx via Starlette's TestClient) is used so the
Uvicorn server itself is never started during unit tests.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db
from central_runtime_v2.config import ActiveWorker, DispatcherConfig, RuntimePaths
from central_runtime_v2.coordination import (
    ClaimRequest,
    ClaimResponse,
    CoordinationConfig,
    CoordinationServer,
    HeartbeatRequest,
    LogChunk,
    ResultSubmission,
    WorkPackage,
)

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN = "test-secret-token"


def _base_task_payload(task_id: str = "REMOTE-TEST-1", *, backend: str | None = None) -> dict:
    meta: dict[str, Any] = {}
    if backend:
        meta["worker_backend"] = backend
    return {
        "task_id": task_id,
        "title": "Coordination test task",
        "summary": "Validate coordination API",
        "objective_md": "Test coordination.",
        "context_md": "Synthetic fixture.",
        "scope_md": "- coordination only",
        "deliverables_md": "- passing tests",
        "acceptance_md": "- tests pass",
        "testing_md": "- pytest -q tests/test_coordination.py",
        "dispatch_md": "Dispatch via API.",
        "closeout_md": "Synthetic closeout.",
        "reconciliation_md": "None.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": "one-off",
        "metadata": {},
        "execution": {
            "task_kind": "mutating",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 3600,
            "metadata": meta,
        },
        "dependencies": [],
    }


class _NullLogger:
    """Logger stub that discards all output."""

    def emit(self, _level: str, _source: str, _msg: str) -> None:
        pass


@dataclass
class MockDispatcherBridge:
    """Mock DispatcherBridge backed by a real SQLite DB for claim/lease tests."""

    db_path: Path
    paths: RuntimePaths
    active_workers: dict[str, ActiveWorker] = field(default_factory=dict)
    active_lock: threading.Lock = field(default_factory=threading.Lock)
    logger: Any = field(default_factory=_NullLogger)
    _dispatcher_config: DispatcherConfig = field(init=False)
    _started_at: float = field(default_factory=time.time)
    _version: str = "abc1234"
    _id: str = "test-dispatcher"

    def __post_init__(self) -> None:
        self._dispatcher_config = DispatcherConfig(
            db_path=self.db_path,
            state_dir=self.paths.state_dir,
            max_workers=4,
            poll_interval=1.0,
            heartbeat_seconds=30.0,
            status_heartbeat_seconds=60.0,
            stale_recovery_seconds=300.0,
            worker_mode="claude",
            default_worker_model="claude-sonnet-4-6",
        )

    @property
    def dispatcher_config(self) -> DispatcherConfig:
        return self._dispatcher_config

    def dispatcher_version(self) -> str:
        return self._version

    def dispatcher_id(self) -> str:
        return self._id

    def started_at(self) -> float:
        return self._started_at


def _make_runtime_paths(tmpdir: Path) -> RuntimePaths:
    state_dir = tmpdir / "state"
    state_dir.mkdir()
    (state_dir / "worker_logs").mkdir()
    (state_dir / "worker_results").mkdir()
    (state_dir / "worker_prompts").mkdir()
    return RuntimePaths(
        state_dir=state_dir,
        lock_path=state_dir / "dispatcher.lock",
        log_path=state_dir / "dispatcher.log",
        worker_status_cache_path=state_dir / "worker_status.json",
        worker_logs_dir=state_dir / "worker_logs",
        worker_results_dir=state_dir / "worker_results",
        worker_prompts_dir=state_dir / "worker_prompts",
    )


def _setup_db(db_path: Path) -> None:
    conn = task_db.connect(db_path)
    try:
        task_db.apply_migrations(
            conn, task_db.load_migrations(task_db.resolve_migrations_dir(None))
        )
        with conn:
            task_db.ensure_repo(
                conn,
                repo_id="CENTRAL",
                repo_root=str(REPO_ROOT),
                display_name="CENTRAL",
            )
    finally:
        conn.close()


def _create_task(db_path: Path, payload: dict) -> None:
    conn = task_db.connect(db_path)
    try:
        with conn:
            task_db.create_task(conn, payload, actor_kind="test", actor_id="test")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationAuthRejection(unittest.TestCase):
    """Invalid or missing bearer tokens must be rejected."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_auth_")
        tmpdir = Path(self._tmpdir.name)
        db_path = tmpdir / "tasks.db"
        _setup_db(db_path)
        paths = _make_runtime_paths(tmpdir)
        bridge = MockDispatcherBridge(db_path=db_path, paths=paths)
        config = CoordinationConfig(token=_TOKEN)
        server = CoordinationServer(bridge, config)
        self._client = TestClient(server._app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_missing_token_rejected(self) -> None:
        resp = self._client.get("/api/v1/status")
        self.assertIn(resp.status_code, (401, 403))

    def test_wrong_token_rejected(self) -> None:
        resp = self._client.get(
            "/api/v1/status",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_correct_token_accepted(self) -> None:
        resp = self._client.get(
            "/api/v1/status",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationClaim(unittest.TestCase):
    """Claim endpoint filtering, cap enforcement, and version handshake."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_claim_")
        tmpdir = Path(self._tmpdir.name)
        self.db_path = tmpdir / "tasks.db"
        _setup_db(self.db_path)
        self.paths = _make_runtime_paths(tmpdir)
        self.bridge = MockDispatcherBridge(db_path=self.db_path, paths=self.paths)
        config = CoordinationConfig(token=_TOKEN, max_remote_workers=2)
        self.server = CoordinationServer(self.bridge, config)
        self._client = TestClient(self.server._app, raise_server_exceptions=True)
        self._auth = {"Authorization": f"Bearer {_TOKEN}"}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _claim(self, backends: list[str], version: str = "abc1234") -> Any:
        resp = self._client.post(
            "/api/v1/claim",
            headers=self._auth,
            json={"worker_id": "wsl2-worker", "backends": backends, "central_version": version},
        )
        return resp

    def test_no_tasks_returns_204(self) -> None:
        resp = self._claim(["claude"])
        self.assertEqual(resp.status_code, 204)

    def test_claim_returns_work_package(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-1"))
        resp = self._claim(["claude"])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("work_package", body)
        wp = body["work_package"]
        self.assertEqual(wp["task_id"], "REMOTE-C-1")
        self.assertEqual(wp["worker_backend"], "claude")
        self.assertIsNotNone(wp["run_id"])
        self.assertIsNotNone(wp["prompt_body"])

    def test_backend_filtering_returns_204_when_no_match(self) -> None:
        # Task has no backend override → defaults to claude (worker_mode).
        # Worker only advertises "codex" → should get 204.
        _create_task(self.db_path, _base_task_payload("REMOTE-C-2"))
        resp = self._claim(["codex"])
        self.assertEqual(resp.status_code, 204)

    def test_per_task_backend_override_respected(self) -> None:
        # Task forces grok backend; worker advertises grok.
        _create_task(self.db_path, _base_task_payload("REMOTE-C-3", backend="grok"))
        resp = self._claim(["grok"])
        self.assertEqual(resp.status_code, 200)
        wp = resp.json()["work_package"]
        self.assertEqual(wp["worker_backend"], "grok")

    def test_global_remote_cap_returns_204_when_full(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-4"))
        # Pre-fill active_workers with 2 remote entries (cap is 2)
        dummy = _make_dummy_worker("REMOTE-C-DUMMY-1")
        dummy2 = _make_dummy_worker("REMOTE-C-DUMMY-2")
        with self.bridge.active_lock:
            self.bridge.active_workers["REMOTE-C-DUMMY-1"] = dummy
            self.bridge.active_workers["REMOTE-C-DUMMY-2"] = dummy2
        resp = self._claim(["claude"])
        self.assertEqual(resp.status_code, 204)

    def test_version_match_has_no_warning(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-5"))
        resp = self._claim(["claude"], version="abc1234")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["version_warning"])

    def test_version_mismatch_returns_stale_warning(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-6"))
        resp = self._claim(["claude"], version="deadbeef")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["version_warning"], "stale")

    def test_task_registered_in_active_workers(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-7"))
        resp = self._claim(["claude"])
        self.assertEqual(resp.status_code, 200)
        task_id = resp.json()["work_package"]["task_id"]
        with self.bridge.active_lock:
            self.assertIn(task_id, self.bridge.active_workers)
            state = self.bridge.active_workers[task_id]
        self.assertTrue(state.is_remote)
        self.assertEqual(state.remote_worker_id, "wsl2-worker")

    def test_claimed_task_not_returned_twice(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-C-8"))
        r1 = self._claim(["claude"])
        self.assertEqual(r1.status_code, 200)
        r2 = self._claim(["claude"])
        self.assertEqual(r2.status_code, 204)


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationHeartbeat(unittest.TestCase):
    """Heartbeat endpoint: lease renewal, 404 on unknown task, 410 on cancellation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_hb_")
        tmpdir = Path(self._tmpdir.name)
        self.db_path = tmpdir / "tasks.db"
        _setup_db(self.db_path)
        self.paths = _make_runtime_paths(tmpdir)
        self.bridge = MockDispatcherBridge(db_path=self.db_path, paths=self.paths)
        config = CoordinationConfig(token=_TOKEN)
        self.server = CoordinationServer(self.bridge, config)
        self._client = TestClient(self.server._app, raise_server_exceptions=False)
        self._auth = {"Authorization": f"Bearer {_TOKEN}"}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _claim_task(self, task_id: str) -> tuple[str, str]:
        """Claim a task and return (task_id, run_id)."""
        _create_task(self.db_path, _base_task_payload(task_id))
        resp = self._client.post(
            "/api/v1/claim",
            headers=self._auth,
            json={"worker_id": "hb-worker", "backends": ["claude"], "central_version": "abc1234"},
        )
        self.assertEqual(resp.status_code, 200)
        wp = resp.json()["work_package"]
        return wp["task_id"], wp["run_id"]

    def _worker_id_for(self, task_id: str) -> str:
        with self.bridge.active_lock:
            return self.bridge.active_workers[task_id].worker_id

    def _heartbeat(self, task_id: str, run_id: str, worker_id: str) -> Any:
        return self._client.post(
            "/api/v1/heartbeat",
            headers=self._auth,
            json={"task_id": task_id, "run_id": run_id, "worker_id": worker_id, "status": "running"},
        )

    def test_heartbeat_renews_lease(self) -> None:
        tid, rid = self._claim_task("REMOTE-H-1")
        wid = self._worker_id_for(tid)
        resp = self._heartbeat(tid, rid, wid)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["lease_renewed"])

    def test_heartbeat_updates_in_memory_timestamp(self) -> None:
        tid, rid = self._claim_task("REMOTE-H-2")
        wid = self._worker_id_for(tid)
        # Record monotonic before heartbeat
        with self.bridge.active_lock:
            before = self.bridge.active_workers[tid].last_heartbeat_monotonic
        resp = self._heartbeat(tid, rid, wid)
        self.assertEqual(resp.status_code, 200)
        with self.bridge.active_lock:
            after = self.bridge.active_workers[tid].last_heartbeat_monotonic
        self.assertGreaterEqual(after, before)

    def test_heartbeat_404_for_unknown_task(self) -> None:
        resp = self._heartbeat("REMOTE-UNKNOWN", "run-x", "some-worker")
        self.assertEqual(resp.status_code, 404)

    def test_heartbeat_410_after_lease_deleted(self) -> None:
        """Simulates cancellation by deleting the lease row from DB."""
        tid, rid = self._claim_task("REMOTE-H-3")
        wid = self._worker_id_for(tid)
        # Delete the lease to simulate cancellation
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM task_active_leases WHERE task_id = ?", (tid,))
        finally:
            conn.close()
        resp = self._heartbeat(tid, rid, wid)
        self.assertEqual(resp.status_code, 410)

    def test_heartbeat_410_for_wrong_worker(self) -> None:
        """A different worker_id gets 410 (lease belongs to another)."""
        tid, rid = self._claim_task("REMOTE-H-4")
        resp = self._heartbeat(tid, rid, "different-worker-id")
        self.assertEqual(resp.status_code, 410)


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationResult(unittest.TestCase):
    """Result endpoint: persistence, finalization queue, duplicate rejection."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_result_")
        tmpdir = Path(self._tmpdir.name)
        self.db_path = tmpdir / "tasks.db"
        _setup_db(self.db_path)
        self.paths = _make_runtime_paths(tmpdir)
        self.bridge = MockDispatcherBridge(db_path=self.db_path, paths=self.paths)
        config = CoordinationConfig(token=_TOKEN)
        self.server = CoordinationServer(self.bridge, config)
        self._client = TestClient(self.server._app, raise_server_exceptions=False)
        self._auth = {"Authorization": f"Bearer {_TOKEN}"}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _submit_result(self, task_id: str, run_id: str, worker_id: str, log_tail: str | None = None) -> Any:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "run_id": run_id,
            "worker_id": worker_id,
            "result": {
                "status": "COMPLETED",
                "summary": "Test result",
                "decisions": ["Chose X"],
                "discoveries": [],
                "warnings": [],
                "completed_items": [],
                "remaining_items": [],
                "files_changed": [],
                "validation": [],
            },
        }
        if log_tail is not None:
            payload["log_tail"] = log_tail
        return self._client.post("/api/v1/result", headers=self._auth, json=payload)

    def test_result_writes_json_file(self) -> None:
        resp = self._submit_result("REMOTE-R-1", "run-001", "worker-1")
        self.assertEqual(resp.status_code, 200)
        result_path = self.paths.worker_results_dir / "REMOTE-R-1" / "run-001.json"
        self.assertTrue(result_path.exists())
        data = json.loads(result_path.read_text())
        self.assertEqual(data["status"], "COMPLETED")

    def test_result_writes_log_tail(self) -> None:
        resp = self._submit_result("REMOTE-R-2", "run-002", "worker-1", log_tail="last line\n")
        self.assertEqual(resp.status_code, 200)
        log_path = self.paths.worker_logs_dir / "REMOTE-R-2" / "run-002.log"
        self.assertTrue(log_path.exists())
        self.assertIn("last line", log_path.read_text())

    def test_result_enqueues_finalization(self) -> None:
        resp = self._submit_result("REMOTE-R-3", "run-003", "worker-1")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.server.finalization_queue.empty())
        task_id, run_id = self.server.finalization_queue.get_nowait()
        self.assertEqual(task_id, "REMOTE-R-3")
        self.assertEqual(run_id, "run-003")

    def test_duplicate_result_returns_409(self) -> None:
        self._submit_result("REMOTE-R-4", "run-004", "worker-1")
        resp2 = self._submit_result("REMOTE-R-4", "run-004", "worker-1")
        self.assertEqual(resp2.status_code, 409)

    def test_no_log_tail_skips_log_file(self) -> None:
        resp = self._submit_result("REMOTE-R-5", "run-005", "worker-1", log_tail=None)
        self.assertEqual(resp.status_code, 200)
        log_path = self.paths.worker_logs_dir / "REMOTE-R-5" / "run-005.log"
        # Log file should not exist (no streaming log and no tail)
        self.assertFalse(log_path.exists())


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationLog(unittest.TestCase):
    """Log chunk appending."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_log_")
        tmpdir = Path(self._tmpdir.name)
        db_path = tmpdir / "tasks.db"
        _setup_db(db_path)
        paths = _make_runtime_paths(tmpdir)
        bridge = MockDispatcherBridge(db_path=db_path, paths=paths)
        config = CoordinationConfig(token=_TOKEN)
        server = CoordinationServer(bridge, config)
        self._client = TestClient(server._app, raise_server_exceptions=True)
        self._auth = {"Authorization": f"Bearer {_TOKEN}"}
        self._paths = paths

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _send_chunk(self, chunk: str) -> Any:
        return self._client.post(
            "/api/v1/log",
            headers=self._auth,
            json={"task_id": "REMOTE-L-1", "run_id": "run-log-1", "chunk": chunk},
        )

    def test_log_chunk_appended(self) -> None:
        r1 = self._send_chunk("line one\n")
        r2 = self._send_chunk("line two\n")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        log_path = self._paths.worker_logs_dir / "REMOTE-L-1" / "run-log-1.log"
        content = log_path.read_text()
        self.assertIn("line one", content)
        self.assertIn("line two", content)


@unittest.skipIf(TestClient is None, "httpx/starlette not installed")
class TestCoordinationStatus(unittest.TestCase):
    """Status endpoint returns correct aggregate counts."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="coord_test_status_")
        tmpdir = Path(self._tmpdir.name)
        self.db_path = tmpdir / "tasks.db"
        _setup_db(self.db_path)
        self.paths = _make_runtime_paths(tmpdir)
        self.bridge = MockDispatcherBridge(db_path=self.db_path, paths=self.paths)
        config = CoordinationConfig(token=_TOKEN)
        self.server = CoordinationServer(self.bridge, config)
        self._client = TestClient(self.server._app, raise_server_exceptions=True)
        self._auth = {"Authorization": f"Bearer {_TOKEN}"}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_status_returns_expected_fields(self) -> None:
        resp = self._client.get("/api/v1/status", headers=self._auth)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in (
            "dispatcher_id",
            "dispatcher_version",
            "active_local_workers",
            "active_remote_workers",
            "remote_agents",
            "eligible_tasks",
            "uptime_seconds",
        ):
            self.assertIn(key, body, f"missing key: {key}")

    def test_status_counts_remote_workers(self) -> None:
        w1 = _make_dummy_worker("T1", worker_id="remote:box-a:123")
        w1.remote_worker_id = "box-a"
        w2 = _make_dummy_worker("T2", worker_id="remote:box-a:456")
        w2.remote_worker_id = "box-a"
        w3 = _make_dummy_worker("T3", is_remote=False)
        with self.bridge.active_lock:
            self.bridge.active_workers["T1"] = w1
            self.bridge.active_workers["T2"] = w2
            self.bridge.active_workers["T3"] = w3
        resp = self._client.get("/api/v1/status", headers=self._auth)
        body = resp.json()
        self.assertEqual(body["active_remote_workers"], 2)
        self.assertEqual(body["active_local_workers"], 1)
        self.assertIn("box-a", body["remote_agents"])
        self.assertEqual(body["remote_agents"]["box-a"]["active"], 2)

    def test_status_eligible_count_increases_with_tasks(self) -> None:
        _create_task(self.db_path, _base_task_payload("REMOTE-S-1"))
        resp = self._client.get("/api/v1/status", headers=self._auth)
        self.assertGreaterEqual(resp.json()["eligible_tasks"], 1)

    def test_status_dispatcher_id_and_version(self) -> None:
        resp = self._client.get("/api/v1/status", headers=self._auth)
        body = resp.json()
        self.assertEqual(body["dispatcher_id"], "test-dispatcher")
        self.assertEqual(body["dispatcher_version"], "abc1234")


# ---------------------------------------------------------------------------
# Helpers for building dummy ActiveWorker instances
# ---------------------------------------------------------------------------


def _make_dummy_worker(
    task_id: str,
    *,
    worker_id: str | None = None,
    is_remote: bool = True,
) -> ActiveWorker:
    return ActiveWorker(
        task={"task_id": task_id},
        worker_id=worker_id or f"remote:dummy:{task_id}",
        run_id=f"{task_id}-run",
        pid=-1,
        proc=None,
        log_handle=None,
        prompt_path=Path("/tmp/dummy.md"),
        result_path=Path("/tmp/dummy.json"),
        log_path=Path("/tmp/dummy.log"),
        process_start_token=None,
        started_at=None,
        start_monotonic=None,
        last_heartbeat_monotonic=time.monotonic(),
        timeout_seconds=3600,
        is_remote=is_remote,
        remote_worker_id="dummy-machine" if is_remote else None,
    )


if __name__ == "__main__":
    unittest.main()
