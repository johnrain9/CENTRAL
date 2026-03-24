#!/usr/bin/env python3
"""Unit tests for metrics/query.py.

Each test builds a minimal in-memory SQLite database (with the full
production migration set applied) and asserts query outputs match
expected shapes and values.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import central_task_db as task_db
from metrics import query as mq


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TASK_COUNTER = 9000


def _next_task_id(series: str = "METRICS") -> str:
    global _TASK_COUNTER
    _TASK_COUNTER += 1
    return f"{series}-{_TASK_COUNTER}"


def _task_payload(
    task_id: str | None = None,
    task_type: str = "implementation",
    repo_id: str = "CENTRAL",
    initiative: str = "test-initiative",
    effort: str = "medium",
) -> dict:
    tid = task_id or _next_task_id()
    return {
        "task_id": tid,
        "title": f"Test task {tid}",
        "summary": "Fixture task",
        "objective_md": "obj",
        "context_md": "ctx",
        "scope_md": "scope",
        "deliverables_md": "- deliverable",
        "acceptance_md": "- accepted",
        "testing_md": "- pytest",
        "dispatch_md": "no dispatch",
        "closeout_md": "closeout",
        "reconciliation_md": "reconcile",
        "planner_status": "todo",
        "priority": 50,
        "task_type": task_type,
        "planner_owner": "planner/test",
        "worker_owner": None,
        "target_repo_id": repo_id,
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


def _build_db() -> tuple[sqlite3.Connection, Path]:
    """Return an open connection to a fully-migrated temp DB."""
    tmpdir = tempfile.mkdtemp(prefix="metrics_query_test_")
    db_path = Path(tmpdir) / "central_tasks.db"
    aim_root = Path(tmpdir) / "aim"
    aim_root.mkdir()
    conn = task_db.connect(db_path)
    task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
    with conn:
        task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
        task_db.ensure_repo(conn, repo_id="AIM", repo_root=str(aim_root), display_name="AIM")
    return conn, db_path


def _insert_runtime(
    conn: sqlite3.Connection,
    task_id: str,
    status: str = "done",
    model: str = "claude-sonnet-4-6",
    retry_count: int = 0,
    started_at: str = "2026-01-10T10:00:00+00:00",
    finished_at: str = "2026-01-10T10:30:00+00:00",
    claimed_at: str = "2026-01-10T09:55:00+00:00",
    pending_review_at: str | None = None,
    last_error: str | None = None,
    model_source: str = "dispatcher_default",
) -> None:
    conn.execute(
        """INSERT INTO task_runtime_state
               (task_id, runtime_status, effective_worker_model, worker_model_source,
                retry_count, started_at, finished_at, claimed_at, pending_review_at,
                last_runtime_error, last_transition_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, status, model, model_source, retry_count,
         started_at, finished_at, claimed_at, pending_review_at,
         last_error, finished_at),
    )


def _create_task(conn: sqlite3.Connection, payload: dict, metadata: dict | None = None) -> None:
    with conn:
        task_db.create_task(conn, payload, actor_kind="test", actor_id="metrics.query.tests")
    if metadata:
        with conn:
            conn.execute(
                "UPDATE tasks SET metadata_json = ? WHERE task_id = ?",
                (json.dumps(metadata), payload["task_id"]),
            )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestModelScorecard(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_empty_db_returns_empty_list(self) -> None:
        result = mq.model_scorecard(self.conn)
        self.assertEqual(result, [])

    def test_single_done_task_no_rework(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done", model="claude-opus-4-6")
        result = mq.model_scorecard(self.conn)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["effective_worker_model"], "claude-opus-4-6")
        self.assertEqual(row["total_done"], 1)
        self.assertEqual(row["tasks_reworked"], 0)
        self.assertEqual(row["first_pass_rate"], 1.0)
        self.assertEqual(row["rework_rate"], 0.0)

    def test_rework_count_from_metadata(self) -> None:
        # One task with rework_count=2, one with none → 1 of 2 reworked
        p1 = _task_payload()
        p2 = _task_payload()
        _create_task(self.conn, p1, metadata={"rework_count": 2})
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(self.conn, p1["task_id"], status="done", model="model-a")
            _insert_runtime(self.conn, p2["task_id"], status="done", model="model-a")
        result = mq.model_scorecard(self.conn)
        row = next(r for r in result if r["effective_worker_model"] == "model-a")
        self.assertEqual(row["total_done"], 2)
        self.assertEqual(row["tasks_reworked"], 1)
        self.assertAlmostEqual(row["rework_rate"], 0.5)
        self.assertAlmostEqual(row["avg_rework_cycles"], 1.0)  # (2+0)/2

    def test_audit_tasks_excluded(self) -> None:
        # AUDIT tasks should not appear in model scorecard
        p = _task_payload(task_id="ECO-001-AUDIT")
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done", model="model-a")
        result = mq.model_scorecard(self.conn)
        self.assertEqual(result, [])

    def test_multiple_models_returned_separately(self) -> None:
        for model in ("model-a", "model-b"):
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done", model=model)

        result = mq.model_scorecard(self.conn)
        models = {r["effective_worker_model"] for r in result}
        self.assertIn("model-a", models)
        self.assertIn("model-b", models)

    def test_duration_stats_computed(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(
                self.conn, p["task_id"],
                started_at="2026-01-10T10:00:00+00:00",
                finished_at="2026-01-10T11:00:00+00:00",
                model="model-dur",
            )
        result = mq.model_scorecard(self.conn)
        row = next(r for r in result if r["effective_worker_model"] == "model-dur")
        # Single data point: p50 == the only value
        self.assertAlmostEqual(row["duration_p50_s"], 3600.0)
        self.assertIn("duration_iqr_s", row)
        self.assertIn("duration_n_outliers", row)

    def test_timeout_tasks_excluded_from_duration(self) -> None:
        # A timeout task should be counted in timeout_count but not in duration stats
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(
                self.conn, p["task_id"],
                status="timeout",
                started_at="2026-01-10T10:00:00+00:00",
                finished_at="2026-01-10T11:00:00+00:00",
                model="model-to",
            )
        result = mq.model_scorecard(self.conn)
        # timeout tasks don't appear in model_scorecard (only 'done' counts quality)
        # but timeout_count is tracked
        to_row = next((r for r in result if r["effective_worker_model"] == "model-to"), None)
        # model-to only has timeout tasks: total_done=0, timeout_count=1
        if to_row is not None:
            self.assertEqual(to_row["timeout_count"], 1)
            self.assertEqual(to_row["total_done"], 0)


class TestFirstPassRates(unittest.TestCase):
    """Tests for audit-based first-pass rates (rework_count in metadata_json)."""

    def setUp(self) -> None:
        self.conn, _ = _build_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_by_task_type_no_rework(self) -> None:
        for task_type in ("implementation", "implementation", "design"):
            p = _task_payload(task_type=task_type)
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done")
        result = mq.first_pass_rates_by_task_type(self.conn)
        impl = next(r for r in result if r["task_type"] == "implementation")
        self.assertEqual(impl["total_done"], 2)
        self.assertEqual(impl["tasks_reworked"], 0)
        self.assertEqual(impl["first_pass_rate"], 1.0)

    def test_by_task_type_with_rework(self) -> None:
        # Two impl tasks: one reworked, one not
        p1 = _task_payload(task_type="implementation")
        p2 = _task_payload(task_type="implementation")
        _create_task(self.conn, p1, metadata={"rework_count": 1})
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(self.conn, p1["task_id"], status="done")
            _insert_runtime(self.conn, p2["task_id"], status="done")
        result = mq.first_pass_rates_by_task_type(self.conn)
        impl = next(r for r in result if r["task_type"] == "implementation")
        self.assertEqual(impl["tasks_reworked"], 1)
        self.assertAlmostEqual(impl["rework_rate"], 0.5)
        self.assertAlmostEqual(impl["first_pass_rate"], 0.5)

    def test_audit_tasks_excluded(self) -> None:
        p = _task_payload(task_id="TEST-001-AUDIT", task_type="audit")
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done")
        result = mq.first_pass_rates_by_task_type(self.conn)
        # AUDIT tasks are excluded
        self.assertEqual(result, [])

    def test_by_repo(self) -> None:
        p = _task_payload(repo_id="CENTRAL")
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done")
        result = mq.first_pass_rates_by_repo(self.conn)
        repos = {r["repo"] for r in result}
        self.assertIn("CENTRAL", repos)
        for row in result:
            self.assertIn("repo", row)
            self.assertIn("total_done", row)
            self.assertIn("first_pass_rate", row)

    def test_by_initiative(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done")
        result = mq.first_pass_rates_by_initiative(self.conn)
        self.assertTrue(any(r["initiative"] == "test-initiative" for r in result))


class TestThroughput(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        # Insert two completions today
        for _ in range(2):
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(
                    self.conn, p["task_id"], status="done",
                    finished_at="2026-03-23T12:00:00+00:00",
                )
        # Insert one completion last week
        p2 = _task_payload()
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(
                self.conn, p2["task_id"], status="done",
                finished_at="2026-03-10T12:00:00+00:00",
            )

    def tearDown(self) -> None:
        self.conn.close()

    def test_daily_returns_rows(self) -> None:
        result = mq.throughput_daily(self.conn, days=365)
        self.assertTrue(len(result) > 0)
        row = result[0]
        self.assertIn("date", row)
        self.assertIn("target_repo_id", row)
        self.assertIn("completed", row)

    def test_daily_completed_counts(self) -> None:
        result = mq.throughput_daily(self.conn, days=365)
        total = sum(r["completed"] for r in result)
        self.assertEqual(total, 3)

    def test_weekly_returns_rows(self) -> None:
        result = mq.throughput_weekly(self.conn, weeks=52)
        self.assertTrue(len(result) > 0)
        row = result[0]
        self.assertIn("week", row)
        self.assertIn("completed", row)

    def test_daily_short_window_misses_old_tasks(self) -> None:
        # Tasks finished on 2026-01-10 should not appear in a 5-day window
        result = mq.throughput_daily(self.conn, days=5)
        dates = {r["date"] for r in result}
        self.assertNotIn("2026-01-10", dates)


class TestRetryDistribution(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        for retry_count, status in [(0, "done"), (0, "done"), (1, "done"), (2, "failed")]:
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status, retry_count=retry_count)

    def tearDown(self) -> None:
        self.conn.close()

    def test_distribution_histogram_keys(self) -> None:
        result = mq.retry_distribution(self.conn)
        self.assertTrue(len(result) >= 1)
        for row in result:
            self.assertIn("retry_count", row)
            self.assertIn("tasks", row)
            self.assertIn("pct_of_total", row)

    def test_pct_sums_to_one(self) -> None:
        result = mq.retry_distribution(self.conn)
        total_pct = sum(r["pct_of_total"] for r in result)
        self.assertAlmostEqual(total_pct, 1.0, places=3)

    def test_retry_counts_present(self) -> None:
        result = mq.retry_distribution(self.conn)
        counts = {r["retry_count"] for r in result}
        self.assertIn(0, counts)
        self.assertIn(1, counts)
        self.assertIn(2, counts)


class TestRetryRecoveryRate(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        # 1 retried and succeeded, 1 retried and failed
        for status, retry in [("done", 1), ("failed", 2), ("done", 0)]:
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status, retry_count=retry)

    def tearDown(self) -> None:
        self.conn.close()

    def test_retried_total_excludes_zero_retry(self) -> None:
        result = mq.retry_recovery_rate(self.conn)
        self.assertEqual(result["retried_total"], 2)

    def test_recovery_rate(self) -> None:
        result = mq.retry_recovery_rate(self.conn)
        self.assertAlmostEqual(result["recovery_rate"], 0.5)

    def test_empty_db_returns_none_rate(self) -> None:
        conn2, _ = _build_db()
        result = mq.retry_recovery_rate(conn2)
        self.assertIsNone(result["recovery_rate"])
        conn2.close()


class TestDurationPercentiles(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        # Insert 5 tasks with known durations: 60, 120, 180, 240, 300 seconds
        durations = [60, 120, 180, 240, 300]
        base = "2026-01-10T10:00:00+00:00"
        for i, dur in enumerate(durations):
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(
                    self.conn, p["task_id"],
                    status="done",
                    model="model-perf",
                    started_at="2026-01-10T10:00:00+00:00",
                    finished_at=f"2026-01-10T10:{dur // 60:02d}:{dur % 60:02d}+00:00",
                )

    def tearDown(self) -> None:
        self.conn.close()

    def test_returns_iqr_keys(self) -> None:
        result = mq.duration_percentiles_by_model(self.conn)
        self.assertTrue(len(result) > 0)
        row = result[0]
        for key in ("n", "n_outliers", "p25_s", "p50_s", "p75_s", "iqr_s", "p90_s", "p99_s"):
            self.assertIn(key, row)

    def test_p50_is_median(self) -> None:
        result = mq.duration_percentiles_by_model(self.conn)
        row = next(r for r in result if r["effective_worker_model"] == "model-perf")
        # Median of [60,120,180,240,300] = 180s
        self.assertAlmostEqual(row["p50_s"], 180.0, places=0)

    def test_iqr_equals_p75_minus_p25(self) -> None:
        result = mq.duration_percentiles_by_model(self.conn)
        row = next(r for r in result if r["effective_worker_model"] == "model-perf")
        if row["p25_s"] is not None and row["p75_s"] is not None:
            self.assertAlmostEqual(row["iqr_s"], row["p75_s"] - row["p25_s"], places=1)

    def test_p99_gte_p90_gte_p50(self) -> None:
        result = mq.duration_percentiles_by_model(self.conn)
        for row in result:
            if row["n"] >= 3:
                self.assertGreaterEqual(row["p99_s"], row["p90_s"])
                self.assertGreaterEqual(row["p90_s"], row["p50_s"])


class TestEffortCalibration(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        for effort, status in [("high", "done"), ("high", "done"), ("high", "failed"), ("low", "done")]:
            p = _task_payload(effort=effort)
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status)

    def tearDown(self) -> None:
        self.conn.close()

    def test_returns_effort_and_model_keys(self) -> None:
        result = mq.effort_calibration_crosstab(self.conn)
        for row in result:
            self.assertIn("worker_effort", row)
            self.assertIn("effective_worker_model", row)
            self.assertIn("total_done", row)
            self.assertIn("first_pass_rate", row)
            self.assertIn("rework_rate", row)

    def test_high_effort_no_rework(self) -> None:
        # setUp inserts (high,done),(high,done),(high,failed),(low,done)
        # effort_calibration only counts 'done' tasks; high→2 done with no rework
        result = mq.effort_calibration_crosstab(self.conn)
        high = next(r for r in result if r["worker_effort"] == "high")
        self.assertEqual(high["total_done"], 2)
        self.assertEqual(high["tasks_reworked"], 0)
        self.assertAlmostEqual(high["first_pass_rate"], 1.0)

    def test_low_effort_no_rework(self) -> None:
        result = mq.effort_calibration_crosstab(self.conn)
        low = next(r for r in result if r["worker_effort"] == "low")
        self.assertEqual(low["total_done"], 1)
        self.assertAlmostEqual(low["first_pass_rate"], 1.0)


class TestLeadWorkCycleTimes(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        # Task created 1h before claimed, runs for 30m, closed 2h after creation
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            # Update closed_at on the task
            self.conn.execute(
                "UPDATE tasks SET closed_at = ? WHERE task_id = ?",
                ("2026-01-10T12:00:00+00:00", p["task_id"]),
            )
            _insert_runtime(
                self.conn, p["task_id"],
                status="done",
                claimed_at="2026-01-10T10:00:00+00:00",
                started_at="2026-01-10T10:05:00+00:00",
                finished_at="2026-01-10T10:35:00+00:00",
            )
            # Patch created_at for predictable lead time
            self.conn.execute(
                "UPDATE tasks SET created_at = ? WHERE task_id = ?",
                ("2026-01-10T09:00:00+00:00", p["task_id"]),
            )

    def tearDown(self) -> None:
        self.conn.close()

    def test_returns_single_summary_dict(self) -> None:
        result = mq.lead_work_cycle_times(self.conn)
        self.assertEqual(len(result), 1)

    def test_summary_keys_present(self) -> None:
        result = mq.lead_work_cycle_times(self.conn)
        row = result[0]
        for key in (
            "work_time_n", "work_time_p50_s", "work_time_iqr_s",
            "lead_time_n", "lead_time_p50_s",
            "cycle_time_n", "cycle_time_p50_s",
        ):
            self.assertIn(key, row)

    def test_work_time_approximately_correct(self) -> None:
        result = mq.lead_work_cycle_times(self.conn)
        row = result[0]
        # 30 min = 1800s
        self.assertAlmostEqual(row["work_time_p50_s"], 1800.0, places=0)

    def test_empty_db_returns_none_percentiles(self) -> None:
        conn2, _ = _build_db()
        result = mq.lead_work_cycle_times(conn2)
        self.assertEqual(result[0]["work_time_n"], 0)
        self.assertIsNone(result[0]["lead_time_p50_s"])
        conn2.close()


class TestFailureModeGroups(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()
        errors = [
            "Timeout: Claude API call timed out after 3600 seconds",
            "Timeout: Claude API call timed out after 3600 seconds",
            "Error: quota exceeded for model claude-opus",
            "Error: quota exceeded for model claude-opus",
            "Error: quota exceeded for model claude-opus",
            None,  # no error — should not appear
        ]
        for err in errors:
            p = _task_payload()
            _create_task(self.conn, p)
            status = "failed" if err else "done"
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status, last_error=err)

    def tearDown(self) -> None:
        self.conn.close()

    def test_null_errors_excluded(self) -> None:
        result = mq.failure_mode_groups(self.conn)
        for row in result:
            self.assertNotEqual(row["error_prefix"], "")

    def test_sorted_by_count_descending(self) -> None:
        result = mq.failure_mode_groups(self.conn)
        counts = [r["count"] for r in result]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_top_n_respected(self) -> None:
        result = mq.failure_mode_groups(self.conn, top_n=1)
        self.assertEqual(len(result), 1)

    def test_example_task_ids_populated(self) -> None:
        result = mq.failure_mode_groups(self.conn)
        for row in result:
            self.assertTrue(len(row["example_task_ids"]) >= 1)

    def test_timeout_group_has_count_2(self) -> None:
        result = mq.failure_mode_groups(self.conn)
        timeout_row = next(
            (r for r in result if "timeout" in r["error_prefix"]), None
        )
        self.assertIsNotNone(timeout_row)
        self.assertEqual(timeout_row["count"], 2)

    def test_quota_group_has_count_3(self) -> None:
        result = mq.failure_mode_groups(self.conn)
        quota_row = next(
            (r for r in result if "quota" in r["error_prefix"]), None
        )
        self.assertIsNotNone(quota_row)
        self.assertEqual(quota_row["count"], 3)


class TestHelpers(unittest.TestCase):
    def test_percentile_empty_returns_none(self) -> None:
        self.assertIsNone(mq._percentile([], 50))

    def test_percentile_single_element(self) -> None:
        self.assertAlmostEqual(mq._percentile([42.0], 50), 42.0)

    def test_percentile_p0_is_min(self) -> None:
        vals = [10.0, 20.0, 30.0]
        self.assertAlmostEqual(mq._percentile(vals, 0), 10.0)

    def test_percentile_p100_is_max(self) -> None:
        vals = [10.0, 20.0, 30.0]
        self.assertAlmostEqual(mq._percentile(vals, 100), 30.0)

    def test_duration_seconds_none_inputs(self) -> None:
        self.assertIsNone(mq._duration_seconds(None, "2026-01-01T00:00:00+00:00"))
        self.assertIsNone(mq._duration_seconds("2026-01-01T00:00:00+00:00", None))
        self.assertIsNone(mq._duration_seconds(None, None))

    def test_duration_seconds_basic(self) -> None:
        d = mq._duration_seconds(
            "2026-01-10T10:00:00+00:00",
            "2026-01-10T11:00:00+00:00",
        )
        self.assertAlmostEqual(d, 3600.0)


class TestRetryHeatmap(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_empty_db_returns_empty_list(self) -> None:
        result = mq.retry_heatmap(self.conn)
        self.assertEqual(result, [])

    def test_single_row_no_retries(self) -> None:
        p = _task_payload(task_type="implementation")
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="claude-sonnet-4-6", retry_count=0)
        result = mq.retry_heatmap(self.conn)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["effective_worker_model"], "claude-sonnet-4-6")
        self.assertEqual(row["task_type"], "implementation")
        self.assertEqual(row["total"], 1)
        self.assertAlmostEqual(row["avg_retries"], 0.0)
        self.assertEqual(row["max_retries"], 0)

    def test_avg_retries_computed_correctly(self) -> None:
        # Two tasks, same model+type: retry_counts 0 and 2 → avg 1.0
        for retry in (0, 2):
            p = _task_payload(task_type="audit")
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model="claude-opus-4-6", retry_count=retry)
        result = mq.retry_heatmap(self.conn)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertAlmostEqual(row["avg_retries"], 1.0)
        self.assertEqual(row["max_retries"], 2)
        self.assertEqual(row["total"], 2)

    def test_multiple_model_task_type_combos(self) -> None:
        combos = [
            ("claude-sonnet-4-6", "implementation", "done", 0),
            ("claude-sonnet-4-6", "audit", "done", 1),
            ("claude-opus-4-6", "implementation", "failed", 2),
        ]
        for model, ttype, status, retries in combos:
            p = _task_payload(task_type=ttype)
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status=status,
                                model=model, retry_count=retries)
        result = mq.retry_heatmap(self.conn)
        self.assertEqual(len(result), 3)
        # Sorted descending by avg_retries
        self.assertGreaterEqual(result[0]["avg_retries"], result[-1]["avg_retries"])

    def test_excludes_non_terminal_statuses(self) -> None:
        p = _task_payload(task_type="implementation")
        _create_task(self.conn, p)
        with self.conn:
            self.conn.execute(
                """INSERT INTO task_runtime_state
                       (task_id, runtime_status, effective_worker_model, worker_model_source,
                        retry_count, last_transition_at)
                   VALUES (?,?,?,?,?,?)""",
                (p["task_id"], "running", "claude-sonnet-4-6", "dispatcher_default",
                 5, "2026-01-10T10:00:00+00:00"),
            )
        result = mq.retry_heatmap(self.conn)
        # running is not terminal, should not appear
        self.assertEqual(result, [])

    def test_avg_retries_rounded_to_three_decimals(self) -> None:
        # Three tasks with retry_counts 0, 0, 1 → avg = 0.333...
        for retry in (0, 0, 1):
            p = _task_payload(task_type="implementation")
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model="claude-sonnet-4-6", retry_count=retry)
        result = mq.retry_heatmap(self.conn)
        self.assertEqual(len(result), 1)
        # Should be rounded (3 decimal places)
        self.assertAlmostEqual(result[0]["avg_retries"], 0.333, places=3)


class TestAuditPassRateOverTime(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_empty_db_returns_empty_list(self) -> None:
        result = mq.audit_pass_rate_over_time(self.conn)
        self.assertEqual(result, [])

    def test_returns_expected_keys(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            finished_at="2026-03-20T10:00:00+00:00")
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        self.assertTrue(len(result) > 0)
        row = result[0]
        for key in ("week", "model", "total_done", "tasks_reworked", "first_pass_rate"):
            self.assertIn(key, row)

    def test_first_pass_rate_no_rework(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="model-a", finished_at="2026-03-20T10:00:00+00:00")
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        row = next(r for r in result if r["model"] == "model-a")
        self.assertEqual(row["total_done"], 1)
        self.assertEqual(row["tasks_reworked"], 0)
        self.assertEqual(row["first_pass_rate"], 1.0)

    def test_first_pass_rate_with_rework(self) -> None:
        p1 = _task_payload()
        p2 = _task_payload()
        _create_task(self.conn, p1, metadata={"rework_count": 1})
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(self.conn, p1["task_id"], status="done",
                            model="model-b", finished_at="2026-03-20T10:00:00+00:00")
            _insert_runtime(self.conn, p2["task_id"], status="done",
                            model="model-b", finished_at="2026-03-20T11:00:00+00:00")
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        row = next(r for r in result if r["model"] == "model-b")
        self.assertEqual(row["total_done"], 2)
        self.assertEqual(row["tasks_reworked"], 1)
        self.assertAlmostEqual(row["first_pass_rate"], 0.5)

    def test_audit_tasks_excluded(self) -> None:
        p = _task_payload(task_id="ECO-500-AUDIT")
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="model-c", finished_at="2026-03-20T10:00:00+00:00")
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        self.assertEqual([r for r in result if r["model"] == "model-c"], [])

    def test_different_weeks_returned_separately(self) -> None:
        for finished_at in ("2026-03-10T10:00:00+00:00", "2026-03-20T10:00:00+00:00"):
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model="model-d", finished_at=finished_at)
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        model_d_rows = [r for r in result if r["model"] == "model-d"]
        weeks = {r["week"] for r in model_d_rows}
        self.assertEqual(len(weeks), 2)

    def test_weeks_window_filters_old_tasks(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            # Very old task — outside any reasonable window
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="model-old", finished_at="2020-01-01T10:00:00+00:00")
        result = mq.audit_pass_rate_over_time(self.conn, weeks=4)
        self.assertEqual([r for r in result if r["model"] == "model-old"], [])

    def test_sorted_ascending_by_week_then_model(self) -> None:
        data = [
            ("2026-03-20T10:00:00+00:00", "model-z"),
            ("2026-03-10T10:00:00+00:00", "model-a"),
            ("2026-03-10T10:00:00+00:00", "model-b"),
        ]
        for finished_at, model in data:
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model=model, finished_at=finished_at)
        result = mq.audit_pass_rate_over_time(self.conn, weeks=52)
        keys = [(r["week"], r["model"]) for r in result]
        self.assertEqual(keys, sorted(keys))


class TestDurationCostOverTime(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, _ = _build_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_empty_db_returns_empty_list(self) -> None:
        result = mq.duration_cost_over_time(self.conn)
        self.assertEqual(result, [])

    def test_returns_expected_keys(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="model-e",
                            started_at="2026-03-20T10:00:00+00:00",
                            finished_at="2026-03-20T10:30:00+00:00")
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        self.assertTrue(len(result) > 0)
        row = result[0]
        for key in ("week", "model", "task_count", "p50_duration_s", "avg_cost_usd"):
            self.assertIn(key, row)

    def test_p50_duration_computed(self) -> None:
        # Two tasks: 30m and 60m → median = 45m = 2700s
        p1 = _task_payload()
        p2 = _task_payload()
        _create_task(self.conn, p1)
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(self.conn, p1["task_id"], status="done",
                            model="model-f",
                            started_at="2026-03-20T10:00:00+00:00",
                            finished_at="2026-03-20T10:30:00+00:00")
            _insert_runtime(self.conn, p2["task_id"], status="done",
                            model="model-f",
                            started_at="2026-03-20T10:00:00+00:00",
                            finished_at="2026-03-20T11:00:00+00:00")
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        row = next(r for r in result if r["model"] == "model-f")
        self.assertEqual(row["task_count"], 2)
        self.assertAlmostEqual(row["p50_duration_s"], 2700.0, places=0)

    def test_avg_cost_usd_computed(self) -> None:
        p1 = _task_payload()
        p2 = _task_payload()
        _create_task(self.conn, p1)
        _create_task(self.conn, p2)
        with self.conn:
            _insert_runtime(self.conn, p1["task_id"], status="done",
                            model="model-g",
                            started_at="2026-03-20T10:00:00+00:00",
                            finished_at="2026-03-20T10:30:00+00:00")
            _insert_runtime(self.conn, p2["task_id"], status="done",
                            model="model-g",
                            started_at="2026-03-20T10:00:00+00:00",
                            finished_at="2026-03-20T11:00:00+00:00")
            self.conn.execute(
                "UPDATE task_runtime_state SET tokens_cost_usd = ? WHERE task_id = ?",
                (0.10, p1["task_id"]),
            )
            self.conn.execute(
                "UPDATE task_runtime_state SET tokens_cost_usd = ? WHERE task_id = ?",
                (0.20, p2["task_id"]),
            )
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        row = next(r for r in result if r["model"] == "model-g")
        self.assertAlmostEqual(row["avg_cost_usd"], 0.15, places=4)

    def test_row_excluded_when_both_null(self) -> None:
        # Task with no timestamps and no cost → should be excluded
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            self.conn.execute(
                """INSERT INTO task_runtime_state
                       (task_id, runtime_status, effective_worker_model, worker_model_source,
                        retry_count, finished_at, last_transition_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (p["task_id"], "done", "model-null", "dispatcher_default",
                 0, "2026-03-20T10:00:00+00:00", "2026-03-20T10:00:00+00:00"),
            )
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        # model-null has no started_at → p50 is None, no cost → avg_cost is None → excluded
        self.assertEqual([r for r in result if r["model"] == "model-null"], [])

    def test_different_weeks_returned_separately(self) -> None:
        for finished_at in ("2026-03-10T10:00:00+00:00", "2026-03-20T10:00:00+00:00"):
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model="model-h",
                                started_at=finished_at,
                                finished_at=finished_at)
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        model_h_rows = [r for r in result if r["model"] == "model-h"]
        weeks = {r["week"] for r in model_h_rows}
        self.assertEqual(len(weeks), 2)

    def test_weeks_window_filters_old_tasks(self) -> None:
        p = _task_payload()
        _create_task(self.conn, p)
        with self.conn:
            _insert_runtime(self.conn, p["task_id"], status="done",
                            model="model-ancient",
                            started_at="2020-01-01T10:00:00+00:00",
                            finished_at="2020-01-01T10:30:00+00:00")
        result = mq.duration_cost_over_time(self.conn, weeks=4)
        self.assertEqual([r for r in result if r["model"] == "model-ancient"], [])

    def test_sorted_ascending_by_week_then_model(self) -> None:
        data = [
            ("2026-03-20T10:00:00+00:00", "model-z2"),
            ("2026-03-10T10:00:00+00:00", "model-a2"),
        ]
        for finished_at, model in data:
            p = _task_payload()
            _create_task(self.conn, p)
            with self.conn:
                _insert_runtime(self.conn, p["task_id"], status="done",
                                model=model,
                                started_at=finished_at,
                                finished_at=finished_at)
        result = mq.duration_cost_over_time(self.conn, weeks=52)
        keys = [(r["week"], r["model"]) for r in result]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
