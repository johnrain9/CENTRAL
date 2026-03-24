#!/usr/bin/env python3
"""Unit tests for metrics/worker_results.py.

Each test builds synthetic worker result dicts or a minimal temp directory of
JSON files and asserts function outputs match expected shapes and values.
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
from metrics import worker_results as wr


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _result(
    task_id: str = "TASK-1",
    status: str = "COMPLETED",
    discoveries: list[str] | None = None,
    blockers: list[str] | None = None,
    validation: list[dict] | None = None,
    completed_items: list[str] | None = None,
    remaining_items: list[str] | None = None,
    files_changed: list[str] | None = None,
    artifacts: list | None = None,
    requirements_assessment: list[dict] | None = None,
    system_fit_assessment: dict | None = None,
) -> dict:
    """Build a minimal worker result dict."""
    return {
        "task_id": task_id,
        "status": status,
        "schema_version": 1,
        "run_id": f"{task_id}-99999",
        "summary": "test",
        "discoveries": discoveries or [],
        "blockers": blockers or [],
        "validation": validation or [],
        "completed_items": completed_items or [],
        "remaining_items": remaining_items or [],
        "files_changed": files_changed or [],
        "artifacts": artifacts or [],
        "requirements_assessment": requirements_assessment or [],
        "system_fit_assessment": system_fit_assessment,
        "decisions": [],
        "warnings": [],
    }


def _write_result(directory: Path, task_id: str, result: dict, timestamp: int = 1000000) -> Path:
    """Write a result dict as a JSON file in the expected layout."""
    task_dir = directory / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / f"{task_id}-{timestamp}.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def _build_db() -> sqlite3.Connection:
    tmpdir = tempfile.mkdtemp(prefix="wr_test_")
    db_path = Path(tmpdir) / "central_tasks.db"
    conn = task_db.connect(db_path)
    task_db.apply_migrations(
        conn, task_db.load_migrations(task_db.resolve_migrations_dir(None))
    )
    with conn:
        task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
    return conn


_TASK_COUNTER = 5000


def _next_id(series: str = "WR") -> str:
    global _TASK_COUNTER
    _TASK_COUNTER += 1
    return f"{series}-{_TASK_COUNTER}"


def _create_task(conn: sqlite3.Connection, task_id: str, task_type: str = "implementation",
                 effort: str = "medium") -> None:
    payload = {
        "task_id": task_id,
        "title": f"Test {task_id}",
        "summary": "test",
        "objective_md": "obj", "context_md": "ctx", "scope_md": "scope",
        "deliverables_md": "- d", "acceptance_md": "- a", "testing_md": "- t",
        "dispatch_md": "dispatch", "closeout_md": "closeout", "reconciliation_md": "reconcile",
        "planner_status": "todo",
        "priority": 50,
        "task_type": task_type,
        "planner_owner": "test",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": "test",
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
    with conn:
        task_db.create_task(conn, payload, actor_kind="test", actor_id="wr.tests")


def _insert_runtime(conn: sqlite3.Connection, task_id: str,
                    model: str = "claude-sonnet-4-6") -> None:
    conn.execute(
        """INSERT INTO task_runtime_state
               (task_id, runtime_status, effective_worker_model, worker_model_source,
                retry_count, started_at, finished_at, claimed_at, last_transition_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (task_id, "done", model, "dispatcher_default", 0,
         "2026-01-10T10:00:00+00:00", "2026-01-10T10:30:00+00:00",
         "2026-01-10T09:55:00+00:00", "2026-01-10T10:30:00+00:00"),
    )


# ---------------------------------------------------------------------------
# TestLoadResults
# ---------------------------------------------------------------------------

class TestLoadResults(unittest.TestCase):
    def test_missing_dir_returns_empty(self) -> None:
        result = wr.load_results(Path("/nonexistent/path/does/not/exist"))
        self.assertEqual(result, [])

    def test_loads_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            r = _result(task_id="TASK-A")
            _write_result(d, "TASK-A", r)
            results = wr.load_results(d)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_id"], "TASK-A")

    def test_source_path_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_result(d, "TASK-B", _result(task_id="TASK-B"))
            results = wr.load_results(d)
        self.assertIn("_source_path", results[0])

    def test_latest_only_returns_one_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_result(d, "TASK-C", _result(task_id="TASK-C"), timestamp=1000)
            _write_result(d, "TASK-C", _result(task_id="TASK-C"), timestamp=2000)
            results_latest = wr.load_results(d, latest_only=True)
            results_all = wr.load_results(d, latest_only=False)
        self.assertEqual(len(results_latest), 1)
        self.assertEqual(len(results_all), 2)

    def test_latest_file_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            r1 = _result(task_id="TASK-D"); r1["summary"] = "old"
            r2 = _result(task_id="TASK-D"); r2["summary"] = "new"
            _write_result(d, "TASK-D", r1, timestamp=1000)
            _write_result(d, "TASK-D", r2, timestamp=2000)
            results = wr.load_results(d, latest_only=True)
        self.assertEqual(results[0]["summary"], "new")

    def test_malformed_json_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            bad_dir = d / "BAD-TASK"
            bad_dir.mkdir()
            (bad_dir / "BAD-TASK-1.json").write_text("{not valid json}", encoding="utf-8")
            _write_result(d, "GOOD-TASK", _result(task_id="GOOD-TASK"))
            results = wr.load_results(d)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_id"], "GOOD-TASK")

    def test_empty_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            results = wr.load_results(Path(tmpdir))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# TestDiscoveryDensity
# ---------------------------------------------------------------------------

class TestDiscoveryDensity(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a", "_task_type": "implementation",
             "discoveries": ["d1", "d2", "d3"]},
            {**_result(task_id="T2"), "_model": "model-a", "_task_type": "design",
             "discoveries": ["d1"]},
            {**_result(task_id="T3"), "_model": "model-b", "_task_type": "implementation",
             "discoveries": []},
        ]

    def test_group_by_model_keys(self) -> None:
        rows = wr.discovery_density(self.results, group_by="model")
        self.assertTrue(len(rows) >= 1)
        for row in rows:
            self.assertIn("model", row)
            self.assertIn("task_count", row)
            self.assertIn("avg_discoveries", row)

    def test_group_by_model_correct_avg(self) -> None:
        rows = wr.discovery_density(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        # model-a has 3 + 1 = 4 discoveries across 2 tasks → avg 2.0
        self.assertAlmostEqual(a_row["avg_discoveries"], 2.0)

    def test_group_by_task_type(self) -> None:
        rows = wr.discovery_density(self.results, group_by="task_type")
        types = {r["task_type"] for r in rows}
        self.assertIn("implementation", types)
        self.assertIn("design", types)

    def test_group_by_none_single_row(self) -> None:
        rows = wr.discovery_density(self.results, group_by="none")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_count"], 3)

    def test_invalid_group_by_raises(self) -> None:
        with self.assertRaises(ValueError):
            wr.discovery_density(self.results, group_by="invalid")

    def test_zero_discoveries_included(self) -> None:
        rows = wr.discovery_density(self.results, group_by="model")
        b_row = next(r for r in rows if r["model"] == "model-b")
        self.assertEqual(b_row["avg_discoveries"], 0.0)
        self.assertEqual(b_row["total_discoveries"], 0)

    def test_empty_results_returns_empty(self) -> None:
        rows = wr.discovery_density([], group_by="model")
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# TestBlockerFrequency
# ---------------------------------------------------------------------------

class TestBlockerFrequency(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            _result(task_id="T1", blockers=["Missing dependency: libssl"]),
            _result(task_id="T2", blockers=["Missing dependency: libssl"]),
            _result(task_id="T3", blockers=["Test suite failing: pytest exit 1"]),
            _result(task_id="T4", blockers=[]),
        ]

    def test_returns_sorted_by_count(self) -> None:
        rows = wr.blocker_frequency(self.results)
        counts = [r["count"] for r in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_top_n_respected(self) -> None:
        rows = wr.blocker_frequency(self.results, top_n=1)
        self.assertEqual(len(rows), 1)

    def test_example_task_ids_populated(self) -> None:
        rows = wr.blocker_frequency(self.results)
        for row in rows:
            self.assertTrue(len(row["example_task_ids"]) >= 1)

    def test_most_common_blocker_correct_count(self) -> None:
        rows = wr.blocker_frequency(self.results)
        top = rows[0]
        self.assertEqual(top["count"], 2)

    def test_keys_present(self) -> None:
        rows = wr.blocker_frequency(self.results)
        for row in rows:
            self.assertIn("blocker_prefix", row)
            self.assertIn("count", row)
            self.assertIn("example_task_ids", row)

    def test_empty_blockers_not_counted(self) -> None:
        rows = wr.blocker_frequency(self.results)
        # Only 2 distinct prefixes
        self.assertEqual(len(rows), 2)


class TestBlockerSummary(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            _result(task_id="T1", blockers=["block one"]),
            _result(task_id="T2", blockers=[]),
            _result(task_id="T3", blockers=["block two", "block three"]),
        ]

    def test_summary_keys(self) -> None:
        summary = wr.blocker_summary(self.results)
        for key in ("total_tasks", "tasks_with_blockers", "blocker_rate", "total_blocker_mentions"):
            self.assertIn(key, summary)

    def test_total_tasks(self) -> None:
        summary = wr.blocker_summary(self.results)
        self.assertEqual(summary["total_tasks"], 3)

    def test_tasks_with_blockers(self) -> None:
        summary = wr.blocker_summary(self.results)
        self.assertEqual(summary["tasks_with_blockers"], 2)

    def test_blocker_rate(self) -> None:
        summary = wr.blocker_summary(self.results)
        self.assertAlmostEqual(summary["blocker_rate"], round(2 / 3, 4))

    def test_total_mentions(self) -> None:
        summary = wr.blocker_summary(self.results)
        self.assertEqual(summary["total_blocker_mentions"], 3)

    def test_empty_returns_none_rate(self) -> None:
        summary = wr.blocker_summary([])
        self.assertIsNone(summary["blocker_rate"])


# ---------------------------------------------------------------------------
# TestValidationPassRates
# ---------------------------------------------------------------------------

class TestValidationPassRates(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            _result(task_id="T1", validation=[
                {"name": "py_compile", "passed": True, "notes": ""},
                {"name": "tests", "passed": True, "notes": ""},
            ]),
            _result(task_id="T2", validation=[
                {"name": "py_compile", "passed": True, "notes": ""},
                {"name": "tests", "passed": False, "notes": "1 failure"},
            ]),
            _result(task_id="T3", validation=[
                {"name": "py_compile", "passed": False, "notes": "syntax error"},
            ]),
        ]

    def test_keys_present(self) -> None:
        rows = wr.validation_pass_rates(self.results)
        for row in rows:
            self.assertIn("check_name", row)
            self.assertIn("sample_size", row)
            self.assertIn("passed", row)
            self.assertIn("failed", row)
            self.assertIn("pass_rate", row)

    def test_py_compile_counts(self) -> None:
        rows = wr.validation_pass_rates(self.results)
        pc = next(r for r in rows if r["check_name"] == "py_compile")
        self.assertEqual(pc["sample_size"], 3)
        self.assertEqual(pc["passed"], 2)
        self.assertEqual(pc["failed"], 1)

    def test_pass_rate_calculation(self) -> None:
        rows = wr.validation_pass_rates(self.results)
        pc = next(r for r in rows if r["check_name"] == "py_compile")
        self.assertAlmostEqual(pc["pass_rate"], round(2 / 3, 4))

    def test_tests_check_counts(self) -> None:
        rows = wr.validation_pass_rates(self.results)
        tc = next(r for r in rows if r["check_name"] == "tests")
        self.assertEqual(tc["sample_size"], 2)
        self.assertEqual(tc["passed"], 1)

    def test_sorted_by_sample_size_desc(self) -> None:
        rows = wr.validation_pass_rates(self.results)
        sizes = [r["sample_size"] for r in rows]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_min_sample_filter(self) -> None:
        rows = wr.validation_pass_rates(self.results, min_sample=3)
        # Only py_compile has 3 samples
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_name"], "py_compile")

    def test_empty_results(self) -> None:
        rows = wr.validation_pass_rates([])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# TestCompletionRatios
# ---------------------------------------------------------------------------

class TestCompletionRatios(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a",
             "completed_items": ["a", "b", "c"], "remaining_items": []},
            {**_result(task_id="T2"), "_model": "model-a",
             "completed_items": ["a"], "remaining_items": ["b"]},
            {**_result(task_id="T3"), "_model": "model-b",
             "completed_items": [], "remaining_items": []},
        ]

    def test_keys_present(self) -> None:
        rows = wr.completion_ratios(self.results, group_by="model")
        for row in rows:
            for key in ("model", "task_count", "fully_complete", "partial",
                        "no_checklist", "avg_completion_ratio"):
                self.assertIn(key, row)

    def test_fully_complete_count(self) -> None:
        rows = wr.completion_ratios(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertEqual(a_row["fully_complete"], 1)
        self.assertEqual(a_row["partial"], 1)

    def test_no_checklist_counted(self) -> None:
        rows = wr.completion_ratios(self.results, group_by="model")
        b_row = next(r for r in rows if r["model"] == "model-b")
        self.assertEqual(b_row["no_checklist"], 1)
        self.assertIsNone(b_row["avg_completion_ratio"])

    def test_avg_ratio_model_a(self) -> None:
        rows = wr.completion_ratios(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        # ratios: 1.0 and 0.5 → avg 0.75
        self.assertAlmostEqual(a_row["avg_completion_ratio"], 0.75)

    def test_group_by_none(self) -> None:
        rows = wr.completion_ratios(self.results, group_by="none")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_count"], 3)


# ---------------------------------------------------------------------------
# TestFilesChangedStats
# ---------------------------------------------------------------------------

class TestFilesChangedStats(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a",
             "files_changed": ["a.py", "b.py", "c.py"]},
            {**_result(task_id="T2"), "_model": "model-a",
             "files_changed": ["x.py"]},
            {**_result(task_id="T3"), "_model": "model-b",
             "files_changed": []},
        ]

    def test_keys_present(self) -> None:
        rows = wr.files_changed_stats(self.results, group_by="model")
        for row in rows:
            for key in ("model", "task_count", "total_files_changed",
                        "avg_files_changed", "p50_files_changed", "zero_files_tasks"):
                self.assertIn(key, row)

    def test_totals_correct(self) -> None:
        rows = wr.files_changed_stats(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertEqual(a_row["total_files_changed"], 4)
        self.assertAlmostEqual(a_row["avg_files_changed"], 2.0)

    def test_zero_files_tasks_counted(self) -> None:
        rows = wr.files_changed_stats(self.results, group_by="model")
        b_row = next(r for r in rows if r["model"] == "model-b")
        self.assertEqual(b_row["zero_files_tasks"], 1)

    def test_p99_gte_p90_gte_p50(self) -> None:
        rows = wr.files_changed_stats(self.results, group_by="none")
        row = rows[0]
        p50 = row["p50_files_changed"]
        p90 = row["p90_files_changed"]
        p99 = row["p99_files_changed"]
        if p50 is not None:
            self.assertGreaterEqual(p90, p50)
            self.assertGreaterEqual(p99, p90)


# ---------------------------------------------------------------------------
# TestArtifactProductionRates
# ---------------------------------------------------------------------------

class TestArtifactProductionRates(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a",
             "artifacts": [{"type": "report", "path": "/a.md", "notes": ""}]},
            {**_result(task_id="T2"), "_model": "model-a",
             "artifacts": []},
            {**_result(task_id="T3"), "_model": "model-b",
             "artifacts": [{"type": "sql", "path": "/b.sql", "notes": ""},
                           {"type": "test", "path": "/c.py", "notes": ""}]},
        ]

    def test_keys_present(self) -> None:
        rows = wr.artifact_production_rates(self.results, group_by="model")
        for row in rows:
            for key in ("model", "task_count", "tasks_with_artifacts",
                        "artifact_rate", "total_artifacts", "avg_artifacts_per_task"):
                self.assertIn(key, row)

    def test_artifact_rate_model_a(self) -> None:
        rows = wr.artifact_production_rates(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertEqual(a_row["task_count"], 2)
        self.assertEqual(a_row["tasks_with_artifacts"], 1)
        self.assertAlmostEqual(a_row["artifact_rate"], 0.5)

    def test_total_artifacts_model_b(self) -> None:
        rows = wr.artifact_production_rates(self.results, group_by="model")
        b_row = next(r for r in rows if r["model"] == "model-b")
        self.assertEqual(b_row["total_artifacts"], 2)
        self.assertAlmostEqual(b_row["artifact_rate"], 1.0)

    def test_empty_list_returns_empty(self) -> None:
        rows = wr.artifact_production_rates([])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# TestRequirementsCoverage
# ---------------------------------------------------------------------------

class TestRequirementsCoverage(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a",
             "requirements_assessment": [
                 {"requirement": "R1", "verdict": "met", "notes": ""},
                 {"requirement": "R2", "verdict": "met", "notes": ""},
                 {"requirement": "R3", "verdict": "not_met", "notes": ""},
             ]},
            {**_result(task_id="T2"), "_model": "model-a",
             "requirements_assessment": [
                 {"requirement": "R1", "verdict": "partially_met", "notes": ""},
                 {"requirement": "R2", "verdict": "not_applicable", "notes": ""},
             ]},
            {**_result(task_id="T3"), "_model": "model-b",
             "requirements_assessment": [
                 {"requirement": "R1", "verdict": "met", "notes": ""},
             ]},
        ]

    def test_keys_present(self) -> None:
        rows = wr.requirements_coverage(self.results, group_by="model")
        for row in rows:
            for key in ("model", "tasks_assessed", "total_requirements",
                        "met", "partially_met", "not_met", "not_applicable",
                        "coverage_rate", "full_coverage_rate"):
                self.assertIn(key, row)

    def test_model_a_counts(self) -> None:
        rows = wr.requirements_coverage(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertEqual(a_row["tasks_assessed"], 2)
        self.assertEqual(a_row["met"], 2)
        self.assertEqual(a_row["partially_met"], 1)
        self.assertEqual(a_row["not_met"], 1)
        self.assertEqual(a_row["not_applicable"], 1)

    def test_coverage_rate_model_a(self) -> None:
        rows = wr.requirements_coverage(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        # denominator = met + partially_met + not_met = 2 + 1 + 1 = 4
        self.assertAlmostEqual(a_row["coverage_rate"], round(2 / 4, 4))

    def test_full_coverage_rate_excludes_not_applicable(self) -> None:
        rows = wr.requirements_coverage(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        # applicable = total(5) - not_applicable(1) = 4
        self.assertAlmostEqual(a_row["full_coverage_rate"], round(2 / 4, 4))

    def test_tasks_without_assessment_excluded(self) -> None:
        results = [_result(task_id="NO-RA")]  # no requirements_assessment
        rows = wr.requirements_coverage(results, group_by="none")
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# TestSystemFitDistribution
# ---------------------------------------------------------------------------

class TestSystemFitDistribution(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {**_result(task_id="T1"), "_model": "model-a",
             "system_fit_assessment": {"verdict": "fit", "notes": "", "local_optimization_risk": "low"}},
            {**_result(task_id="T2"), "_model": "model-a",
             "system_fit_assessment": {"verdict": "partial_fit", "notes": "", "local_optimization_risk": "medium"}},
            {**_result(task_id="T3"), "_model": "model-b",
             "system_fit_assessment": {"verdict": "fit", "notes": "", "local_optimization_risk": "low"}},
            _result(task_id="T4"),  # no system_fit_assessment
        ]

    def test_keys_present(self) -> None:
        rows = wr.system_fit_distribution(self.results, group_by="model")
        for row in rows:
            for key in ("model", "assessed_tasks", "fit", "partial_fit", "not_fit",
                        "fit_rate", "risk_low", "risk_medium", "risk_high"):
                self.assertIn(key, row)

    def test_model_a_counts(self) -> None:
        rows = wr.system_fit_distribution(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertEqual(a_row["assessed_tasks"], 2)
        self.assertEqual(a_row["fit"], 1)
        self.assertEqual(a_row["partial_fit"], 1)
        self.assertEqual(a_row["risk_low"], 1)
        self.assertEqual(a_row["risk_medium"], 1)

    def test_fit_rate(self) -> None:
        rows = wr.system_fit_distribution(self.results, group_by="model")
        a_row = next(r for r in rows if r["model"] == "model-a")
        self.assertAlmostEqual(a_row["fit_rate"], 0.5)

    def test_unassessed_results_excluded(self) -> None:
        rows = wr.system_fit_distribution(self.results, group_by="none")
        self.assertEqual(len(rows), 1)
        # T4 has no system_fit_assessment → only 3 assessed
        self.assertEqual(rows[0]["assessed_tasks"], 3)

    def test_empty_returns_empty(self) -> None:
        rows = wr.system_fit_distribution([], group_by="none")
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# TestCorrelateWithDb
# ---------------------------------------------------------------------------

class TestCorrelateWithDb(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _build_db()
        self.task_id = _next_id("CORR")
        _create_task(self.conn, self.task_id, task_type="design", effort="high")
        with self.conn:
            _insert_runtime(self.conn, self.task_id, model="claude-opus-4-6")

    def tearDown(self) -> None:
        self.conn.close()

    def test_db_fields_injected(self) -> None:
        results = [_result(task_id=self.task_id)]
        enriched = wr.correlate_with_db(results, self.conn)
        self.assertEqual(len(enriched), 1)
        row = enriched[0]
        self.assertEqual(row["_model"], "claude-opus-4-6")
        self.assertEqual(row["_task_type"], "design")
        self.assertEqual(row["_worker_effort"], "high")
        self.assertEqual(row["_target_repo_id"], "CENTRAL")

    def test_unknown_task_id_gets_unknown(self) -> None:
        results = [_result(task_id="DOES-NOT-EXIST")]
        enriched = wr.correlate_with_db(results, self.conn)
        row = enriched[0]
        self.assertEqual(row["_model"], "unknown")
        self.assertEqual(row["_task_type"], "unknown")

    def test_original_fields_preserved(self) -> None:
        results = [_result(task_id=self.task_id, status="COMPLETED")]
        enriched = wr.correlate_with_db(results, self.conn)
        self.assertEqual(enriched[0]["status"], "COMPLETED")
        self.assertEqual(enriched[0]["task_id"], self.task_id)

    def test_empty_results_returns_empty(self) -> None:
        enriched = wr.correlate_with_db([], self.conn)
        self.assertEqual(enriched, [])

    def test_enrichment_enables_model_grouping(self) -> None:
        results = [_result(task_id=self.task_id, discoveries=["d1", "d2"])]
        enriched = wr.correlate_with_db(results, self.conn)
        rows = wr.discovery_density(enriched, group_by="model")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "claude-opus-4-6")
        self.assertEqual(rows[0]["total_discoveries"], 2)


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_percentile_empty_returns_none(self) -> None:
        self.assertIsNone(wr._percentile([], 50))

    def test_percentile_single_element(self) -> None:
        self.assertAlmostEqual(wr._percentile([42.0], 50), 42.0)

    def test_percentile_p0_is_min(self) -> None:
        self.assertAlmostEqual(wr._percentile([10.0, 20.0, 30.0], 0), 10.0)

    def test_percentile_p100_is_max(self) -> None:
        self.assertAlmostEqual(wr._percentile([10.0, 20.0, 30.0], 100), 30.0)

    def test_safe_list_with_list(self) -> None:
        self.assertEqual(wr._safe_list([1, 2, 3]), [1, 2, 3])

    def test_safe_list_with_none(self) -> None:
        self.assertEqual(wr._safe_list(None), [])

    def test_safe_str_with_str(self) -> None:
        self.assertEqual(wr._safe_str("hello"), "hello")

    def test_safe_str_with_none(self) -> None:
        self.assertEqual(wr._safe_str(None), "")


if __name__ == "__main__":
    unittest.main()
