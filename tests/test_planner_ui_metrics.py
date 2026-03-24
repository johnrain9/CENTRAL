#!/usr/bin/env python3
"""Unit tests for the /api/metrics/all endpoint in scripts/planner_ui.py."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import central_task_db as task_db

# Import after path setup so Flask app is importable
import planner_ui as ui


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TASK_COUNTER = 8000


def _next_task_id(series: str = "MTEST") -> str:
    global _TASK_COUNTER
    _TASK_COUNTER += 1
    return f"{series}-{_TASK_COUNTER}"


def _build_db() -> tuple[sqlite3.Connection, str]:
    """Return a fully-migrated temp DB connection and its path."""
    tmpdir = tempfile.mkdtemp(prefix="ui_metrics_test_")
    db_path = Path(tmpdir) / "central_tasks.db"
    conn = task_db.connect(db_path)
    task_db.apply_migrations(
        conn, task_db.load_migrations(task_db.resolve_migrations_dir(None))
    )
    with conn:
        task_db.ensure_repo(
            conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL"
        )
    return conn, str(db_path)


def _task_payload(
    task_id: str | None = None,
    task_type: str = "implementation",
    initiative: str = "test",
    effort: str = "medium",
) -> dict:
    tid = task_id or _next_task_id()
    return {
        "task_id": tid,
        "title": f"Test task {tid}",
        "summary": "fixture",
        "objective_md": "obj",
        "context_md": "ctx",
        "scope_md": "scope",
        "deliverables_md": "- deliverable",
        "acceptance_md": "- accepted",
        "testing_md": "- pytest",
        "dispatch_md": "dispatch",
        "closeout_md": "closeout",
        "reconciliation_md": "reconcile",
        "planner_status": "todo",
        "priority": 50,
        "task_type": task_type,
        "planner_owner": "planner/test",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": initiative,
        "metadata": {"audit_required": False},
        "execution": {
            "task_kind": "read_write",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 3600,
            "metadata": {"worker_effort": effort},
        },
        "dependencies": [],
    }


def _insert_runtime(
    conn: sqlite3.Connection,
    task_id: str,
    status: str = "done",
    model: str = "claude-sonnet-4-6",
    retry_count: int = 0,
    started_at: str = "2026-01-10T10:00:00+00:00",
    finished_at: str = "2026-01-10T10:30:00+00:00",
    last_error: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO task_runtime_state
               (task_id, runtime_status, effective_worker_model, worker_model_source,
                retry_count, started_at, finished_at, claimed_at,
                last_runtime_error, last_transition_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id, status, model, "dispatcher_default", retry_count,
            started_at, finished_at, started_at, last_error, finished_at,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetricsEndpointNoDB(unittest.TestCase):
    """Endpoint behaviour when the DB file does not exist."""

    def setUp(self) -> None:
        ui.app.config["TESTING"] = True
        self.client = ui.app.test_client()
        self._orig_env = os.environ.get("CENTRAL_TASK_DB_PATH")
        os.environ["CENTRAL_TASK_DB_PATH"] = "/tmp/nonexistent_db_metrics_test.db"

    def tearDown(self) -> None:
        if self._orig_env is None:
            os.environ.pop("CENTRAL_TASK_DB_PATH", None)
        else:
            os.environ["CENTRAL_TASK_DB_PATH"] = self._orig_env

    def test_returns_503_when_db_absent(self) -> None:
        resp = self.client.get("/api/metrics/all")
        self.assertEqual(resp.status_code, 503)
        body = json.loads(resp.data)
        self.assertIn("error", body)


class TestMetricsEndpointEmptyDB(unittest.TestCase):
    """Endpoint returns correct structure with an empty but valid DB."""

    def setUp(self) -> None:
        ui.app.config["TESTING"] = True
        self.client = ui.app.test_client()
        self.conn, self.db_path = _build_db()
        self._orig_env = os.environ.get("CENTRAL_TASK_DB_PATH")
        os.environ["CENTRAL_TASK_DB_PATH"] = self.db_path

    def tearDown(self) -> None:
        self.conn.close()
        if self._orig_env is None:
            os.environ.pop("CENTRAL_TASK_DB_PATH", None)
        else:
            os.environ["CENTRAL_TASK_DB_PATH"] = self._orig_env

    def test_returns_200(self) -> None:
        resp = self.client.get("/api/metrics/all")
        self.assertEqual(resp.status_code, 200)

    def test_response_has_required_keys(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        expected_keys = {
            "generated_at", "model_scorecard", "effort_calibration",
            "initiative_health", "daily_throughput", "ops_failure_taxonomy",
            "retry_heatmap", "worker_richness", "audit_verdicts",
        }
        self.assertEqual(expected_keys, set(body.keys()))

    def test_empty_db_lists_are_empty(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        self.assertEqual(body["model_scorecard"], [])
        self.assertEqual(body["effort_calibration"], [])
        self.assertEqual(body["initiative_health"], [])
        self.assertEqual(body["daily_throughput"], [])
        self.assertEqual(body["ops_failure_taxonomy"], [])
        self.assertEqual(body["retry_heatmap"], [])
        # audit_verdicts reads from filesystem (worker result files), not just DB
        self.assertIsInstance(body["audit_verdicts"], list)

    def test_worker_richness_has_correct_structure(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        wr = body["worker_richness"]
        self.assertIn("discovery_density", wr)
        self.assertIn("files_changed_stats", wr)


class TestMetricsEndpointWithData(unittest.TestCase):
    """Endpoint returns populated data when tasks + runtime rows exist."""

    def setUp(self) -> None:
        ui.app.config["TESTING"] = True
        self.client = ui.app.test_client()
        self.conn, self.db_path = _build_db()
        self._orig_env = os.environ.get("CENTRAL_TASK_DB_PATH")
        os.environ["CENTRAL_TASK_DB_PATH"] = self.db_path

        # Seed two tasks with runtime
        for status, model, retry in [("done", "claude-sonnet-4-6", 0),
                                     ("failed", "claude-opus-4-6", 1)]:
            p = _task_payload()
            with self.conn:
                task_db.create_task(self.conn, p, actor_kind="test", actor_id="test")
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status,
                                model=model, retry_count=retry)

    def tearDown(self) -> None:
        self.conn.close()
        if self._orig_env is None:
            os.environ.pop("CENTRAL_TASK_DB_PATH", None)
        else:
            os.environ["CENTRAL_TASK_DB_PATH"] = self._orig_env

    def test_model_scorecard_has_one_model(self) -> None:
        # Only 'done' tasks appear in model_scorecard; the failed opus task is excluded
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        self.assertEqual(len(body["model_scorecard"]), 1)
        self.assertEqual(body["model_scorecard"][0]["effective_worker_model"], "claude-sonnet-4-6")

    def test_retry_heatmap_populated(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        heatmap = body["retry_heatmap"]
        self.assertGreater(len(heatmap), 0)
        row = heatmap[0]
        self.assertIn("effective_worker_model", row)
        self.assertIn("task_type", row)
        self.assertIn("avg_retries", row)
        self.assertIn("max_retries", row)
        self.assertIn("total", row)

    def test_ops_failure_taxonomy_populated(self) -> None:
        # Insert a process-level failure with an error string
        p = _task_payload()
        with self.conn:
            task_db.create_task(self.conn, p, actor_kind="test", actor_id="test")
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="failed",
                            last_error="tests failed: assertion error")
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        self.assertGreater(len(body["ops_failure_taxonomy"]), 0)
        row = body["ops_failure_taxonomy"][0]
        self.assertIn("error_prefix", row)
        self.assertIn("count", row)
        self.assertIn("example_task_ids", row)

    def test_generated_at_is_present(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        self.assertIn("generated_at", body)
        self.assertTrue(body["generated_at"])


class TestDailyThroughputWindow(unittest.TestCase):
    """daily_throughput API uses 84-day window for heatmap coverage."""

    def setUp(self) -> None:
        from datetime import datetime, timezone, timedelta
        ui.app.config["TESTING"] = True
        self.client = ui.app.test_client()
        self.conn, self.db_path = _build_db()
        self._orig_env = os.environ.get("CENTRAL_TASK_DB_PATH")
        os.environ["CENTRAL_TASK_DB_PATH"] = self.db_path

        # Insert a 'done' task finished 80 days ago (within 84-day window)
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=80)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        old_date = (now - timedelta(days=80)).strftime("%Y-%m-%d")
        self._old_date = old_date
        p = _task_payload()
        with self.conn:
            task_db.create_task(self.conn, p, actor_kind="test", actor_id="test")
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            started_at=old_ts, finished_at=old_ts)

        # Insert a task finished 95 days ago (outside 84-day window)
        very_old_ts = (now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        p2 = _task_payload()
        with self.conn:
            task_db.create_task(self.conn, p2, actor_kind="test", actor_id="test")
        with self.conn:
            _insert_runtime(self.conn, p2["task_id"], status="done",
                            started_at=very_old_ts, finished_at=very_old_ts)

    def tearDown(self) -> None:
        self.conn.close()
        if self._orig_env is None:
            os.environ.pop("CENTRAL_TASK_DB_PATH", None)
        else:
            os.environ["CENTRAL_TASK_DB_PATH"] = self._orig_env

    def test_daily_throughput_includes_84_day_old_task(self) -> None:
        """Task from 80 days ago appears in daily_throughput."""
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        dates = [r["date"] for r in body["daily_throughput"]]
        self.assertIn(self._old_date, dates)

    def test_daily_throughput_excludes_95_day_old_task(self) -> None:
        """Task from 95 days ago is excluded (outside 84-day window)."""
        from datetime import datetime, timezone, timedelta
        very_old_date = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d")
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        dates = [r["date"] for r in body["daily_throughput"]]
        self.assertNotIn(very_old_date, dates)

    def test_daily_throughput_has_required_fields(self) -> None:
        resp = self.client.get("/api/metrics/all")
        body = json.loads(resp.data)
        self.assertGreater(len(body["daily_throughput"]), 0)
        row = body["daily_throughput"][0]
        self.assertIn("date", row)
        self.assertIn("target_repo_id", row)
        self.assertIn("completed", row)


if __name__ == "__main__":
    unittest.main()
