#!/usr/bin/env python3
"""Coverage boost tests — targets remaining uncovered lines to reach 70%.

Focused on: task-batch-create, dep-show/dep-graph text output, export-repo-md,
task_scaffold_keywords/entrypoints, render_capability_rows/detail,
dep-lint with warnings, and other small gaps.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "central_task_db.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def _base_payload(task_id: str, *, initiative: str = "one-off") -> dict:
    return {
        "task_id": task_id,
        "title": f"{task_id} boost coverage",
        "summary": "Boost coverage task.",
        "objective_md": "Exercise `scripts/central_task_db.py` additional paths.",
        "context_md": "Temporary fixture only.",
        "scope_md": "No production mutation. See `scripts/` folder.",
        "deliverables_md": "- assertions",
        "acceptance_md": "- paths coherent",
        "testing_md": "- automated unittest only",
        "dispatch_md": f"Dispatch from CENTRAL repo=CENTRAL do task {task_id}.",
        "closeout_md": "Synthetic closeout.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 2,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": initiative,
        "metadata": {"audit_required": False},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 60,
            "metadata": {},
        },
        "dependencies": [],
    }


class _BaseDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_db_boost_")
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cli(self, *args: str, check: bool = True) -> CliResult:
        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = int(task_db.main([str(CLI), args[0], "--db-path", str(self.db_path), *args[1:]]))
        except SystemExit as exc:
            returncode = int(exc.code) if isinstance(exc.code, int) else 1
        result = CliResult(returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        if check and result.returncode != 0:
            self.fail(f"central_task_db {' '.join(args)} failed (rc={result.returncode}): {result.stderr or result.stdout}")
        return result

    def create_task(self, task_id: str, **kwargs) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(conn, _base_payload(task_id, **kwargs), actor_kind="test", actor_id="boost.tests")
        finally:
            conn.close()

    def write_json(self, payload: dict) -> Path:
        f = self.tmp_path / f"payload_{id(payload)}.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        return f

    def fetch_snapshot(self, task_id: str) -> dict:
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn, task_id=task_id)
            return snaps[0]
        finally:
            conn.close()


# ===========================================================================
# 1. task-batch-create
# ===========================================================================

class TestBatchTaskCreate(_BaseDbTest):

    def _batch_doc(self) -> dict:
        return {
            "series": "CENTRAL-OPS",
            "repo": "CENTRAL",
            "defaults": {
                "priority": 3,
                "initiative": "batch-test",
                "task_type": "implementation",
            },
            "tasks": [
                {
                    "title": "Batch task one",
                    "objective": "Exercise batch task creation path one.",
                    "metadata": {"audit_required": False},
                },
                {
                    "title": "Batch task two",
                    "objective": "Exercise batch task creation path two.",
                    "metadata": {"audit_required": False},
                },
            ],
        }

    def test_batch_create_dry_run(self) -> None:
        batch_file = self.write_json(self._batch_doc())
        result = self.run_cli(
            "task-batch-create",
            "--input", str(batch_file),
            "--series", "CENTRAL-OPS",
            "--repo", "CENTRAL",
            "--actor-id", "planner/test",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0)
        # dry-run should not persist
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        self.assertEqual(len(snaps), 0)

    def test_batch_create_writes_tasks(self) -> None:
        batch_file = self.write_json(self._batch_doc())
        result = self.run_cli(
            "task-batch-create",
            "--input", str(batch_file),
            "--series", "CENTRAL-OPS",
            "--repo", "CENTRAL",
            "--actor-id", "planner/test",
        )
        self.assertEqual(result.returncode, 0)
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        self.assertEqual(len(snaps), 2)


# ===========================================================================
# 2. dep-show text formatter (lines 7484-7499)
# ===========================================================================

class TestDepShowTextFormatter(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22001")

    def test_dep_show_text_output(self) -> None:
        """Text output for dep-show hits the formatter code."""
        result = self.run_cli("dep-show", "--task-id", "CENTRAL-OPS-22001")
        self.assertEqual(result.returncode, 0)
        # Should contain task info
        output = result.stdout
        self.assertIn("CENTRAL-OPS-22001", output)

    def test_dep_show_json_output(self) -> None:
        result = self.run_cli("dep-show", "--task-id", "CENTRAL-OPS-22001", "--json")
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-22001")
        self.assertIn("depends_on", data)
        self.assertIn("depended_on_by", data)


# ===========================================================================
# 3. dep-graph text formatter (lines 7527-7538)
# ===========================================================================

class TestDepGraphTextFormatter(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        # Create two tasks with a dependency between them
        self.create_task("CENTRAL-OPS-22010")
        self.create_task("CENTRAL-OPS-22011")
        # Add CENTRAL-OPS-22011 depending on CENTRAL-OPS-22010
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id, dependency_kind, created_at) VALUES (?, ?, ?, ?)",
                    ("CENTRAL-OPS-22011", "CENTRAL-OPS-22010", "requires", task_db.now_iso()),
                )
        finally:
            conn.close()

    def test_dep_graph_text_with_edges(self) -> None:
        result = self.run_cli("dep-graph")
        self.assertEqual(result.returncode, 0)
        # Should show dependency edges
        output = result.stdout
        self.assertIn("CENTRAL-OPS-22011", output)

    def test_dep_graph_json_with_edges(self) -> None:
        result = self.run_cli("dep-graph", "--json")
        edges = json.loads(result.stdout)
        self.assertIsInstance(edges, list)
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0]["task_id"], "CENTRAL-OPS-22011")

    def test_dep_graph_include_done_flag(self) -> None:
        result = self.run_cli("dep-graph", "--include-done", "--json")
        edges = json.loads(result.stdout)
        self.assertIsInstance(edges, list)


# ===========================================================================
# 4. dep-lint with warnings
# ===========================================================================

class TestDepLintWithWarnings(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        # Create a task that mentions another task ID in text but no declared dep
        self.create_task("CENTRAL-OPS-22020")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                # Update objective to mention another task ID
                conn.execute(
                    "UPDATE tasks SET objective_md=? WHERE task_id=?",
                    ("This depends on work from CENTRAL-OPS-22021.", "CENTRAL-OPS-22020"),
                )
            with conn:
                # Create the mentioned task
                task_db.create_task(
                    conn,
                    _base_payload("CENTRAL-OPS-22021"),
                    actor_kind="test",
                    actor_id="boost.tests",
                )
        finally:
            conn.close()

    def test_dep_lint_finds_missing_edge(self) -> None:
        result = self.run_cli("dep-lint", check=False)
        # Should return 1 and emit warnings
        self.assertEqual(result.returncode, 1)

    def test_dep_lint_json_with_warnings(self) -> None:
        result = self.run_cli("dep-lint", "--json", check=False)
        warnings = json.loads(result.stdout)
        self.assertIsInstance(warnings, list)
        self.assertGreater(len(warnings), 0)


# ===========================================================================
# 5. export-repo-md
# ===========================================================================

class TestExportRepoMd(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22030")
        self.outdir = self.tmp_path / "repo_exports"
        self.outdir.mkdir()

    def test_export_repo_md_produces_file(self) -> None:
        out = self.outdir / "central.md"
        result = self.run_cli(
            "export-repo-md",
            "--repo-id", "CENTRAL",
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")
        self.assertIn("CENTRAL", content)


# ===========================================================================
# 6. task_scaffold_keywords and task_scaffold_entrypoints
# ===========================================================================

class TestTaskScaffoldHelpers(unittest.TestCase):

    def _payload(self) -> dict:
        return {
            "title": "Test task for scaffold",
            "summary": "A scaffold test.",
            "objective_md": "Implement `scripts/central_task_db.py` enhancements.",
            "scope_md": "Limit to `scripts/` directory changes.",
            "deliverables_md": "- Updated `scripts/central_task_db.py`",
            "dispatch_md": "Run `python3 scripts/central_task_db.py init`",
        }

    def test_task_scaffold_keywords_returns_list(self) -> None:
        result = task_db.task_scaffold_keywords(self._payload())
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_task_scaffold_keywords_respects_limit(self) -> None:
        result = task_db.task_scaffold_keywords(self._payload(), limit=3)
        self.assertLessEqual(len(result), 3)

    def test_task_scaffold_entrypoints_returns_list(self) -> None:
        result = task_db.task_scaffold_entrypoints(self._payload())
        self.assertIsInstance(result, list)
        # Should find backtick-wrapped items
        self.assertGreater(len(result), 0)

    def test_task_scaffold_entrypoints_respects_limit(self) -> None:
        result = task_db.task_scaffold_entrypoints(self._payload(), limit=2)
        self.assertLessEqual(len(result), 2)

    def test_task_scaffold_keywords_empty_payload(self) -> None:
        result = task_db.task_scaffold_keywords({})
        self.assertIsInstance(result, list)

    def test_task_scaffold_entrypoints_empty_payload(self) -> None:
        result = task_db.task_scaffold_entrypoints({})
        self.assertIsInstance(result, list)


# ===========================================================================
# 7. render_capability_rows and render_capability_detail
# ===========================================================================

class TestCapabilityRenderHelpers(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        # Create a task and capability for testing
        self.create_task("CENTRAL-OPS-22040")
        cap_payload = {
            "capability_id": "boost_test_cap",
            "name": "Boost Test Capability",
            "kind": "reporting_surface",
            "scope_kind": "workflow",
            "summary": "Capability for render tests.",
            "when_to_use_md": "Use for render testing.",
            "do_not_use_for_md": "Not for production.",
            "evidence_summary_md": "Seeded from boost test.",
            "owning_repo_id": "CENTRAL",
            "affected_repo_ids": ["CENTRAL"],
            "entrypoints": ["scripts/central_task_db.py"],
            "keywords": ["boost", "test"],
            "source_tasks": [{"task_id": "CENTRAL-OPS-22040", "relationship_kind": "seeded_from"}],
            "verification_level": "planner_verified",
            "verified_by_task_id": "CENTRAL-OPS-22040",
            "status": "proposed",
            "metadata": {},
        }
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_capability(conn, cap_payload, actor_kind="planner", actor_id="boost.tests")
        finally:
            conn.close()

    def test_capability_list_text_output(self) -> None:
        result = self.run_cli("capability-list")
        self.assertEqual(result.returncode, 0)

    def test_capability_show_text_output(self) -> None:
        result = self.run_cli("capability-show", "--capability-id", "boost_test_cap")
        self.assertEqual(result.returncode, 0)
        output = result.stdout
        self.assertIn("boost_test_cap", output)

    def test_render_capability_rows_returns_string(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            registry = task_db.fetch_capability_registry(conn)
        finally:
            conn.close()
        output = task_db.render_capability_rows(registry)
        self.assertIsInstance(output, str)
        self.assertIn("boost_test_cap", output)

    def test_render_capability_detail_returns_string(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            cap = task_db.fetch_capability_payload(conn, "boost_test_cap")
        finally:
            conn.close()
        self.assertIsNotNone(cap)
        output = task_db.render_capability_detail(cap)
        self.assertIsInstance(output, str)
        self.assertIn("boost_test_cap", output)


# ===========================================================================
# 8. load_batch_document error paths
# ===========================================================================

class TestLoadBatchDocument(unittest.TestCase):

    def test_load_batch_document_from_json_list(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([{"title": "Task A"}, {"title": "Task B"}], f)
            fname = f.name
        try:
            doc = task_db.load_batch_document(fname)
            self.assertIn("tasks", doc)
            self.assertEqual(len(doc["tasks"]), 2)
        finally:
            Path(fname).unlink()

    def test_load_batch_document_from_json_dict(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"tasks": [{"title": "Task A"}], "series": "TEST"}, f)
            fname = f.name
        try:
            doc = task_db.load_batch_document(fname)
            self.assertEqual(doc["series"], "TEST")
            self.assertEqual(len(doc["tasks"]), 1)
        finally:
            Path(fname).unlink()


# ===========================================================================
# 9. insert_artifact direct
# ===========================================================================

class TestInsertArtifact(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22050")

    def test_insert_artifact_creates_artifact(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.insert_artifact(
                    conn,
                    task_id="CENTRAL-OPS-22050",
                    artifact_kind="test_artifact",
                    path_or_uri="/tmp/test_artifact.txt",
                    label="test_artifact.txt",
                    metadata={"test": True},
                )
            artifacts = task_db.fetch_artifacts(conn, "CENTRAL-OPS-22050")
        finally:
            conn.close()
        self.assertGreater(len(artifacts), 0)
        self.assertEqual(artifacts[0]["artifact_kind"], "test_artifact")


# ===========================================================================
# 10. insert_event direct
# ===========================================================================

class TestInsertEvent(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22060")

    def test_insert_event_creates_event(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.insert_event(
                    conn,
                    task_id="CENTRAL-OPS-22060",
                    event_type="test.custom_event",
                    actor_kind="test",
                    actor_id="boost.tests",
                    payload={"key": "value"},
                )
            events = task_db.fetch_latest_events(conn, "CENTRAL-OPS-22060", limit=20)
        finally:
            conn.close()
        event_types = [e["event_type"] for e in events]
        self.assertIn("test.custom_event", event_types)


# ===========================================================================
# 11. load_dependencies
# ===========================================================================

class TestLoadDependencies(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22070")
        self.create_task("CENTRAL-OPS-22071")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id, dependency_kind, created_at) VALUES (?, ?, ?, ?)",
                    ("CENTRAL-OPS-22071", "CENTRAL-OPS-22070", "requires", task_db.now_iso()),
                )
        finally:
            conn.close()

    def test_load_dependencies_returns_dict(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            deps = task_db.load_dependencies(conn, ["CENTRAL-OPS-22071"])
        finally:
            conn.close()
        self.assertIn("CENTRAL-OPS-22071", deps)
        self.assertEqual(len(deps["CENTRAL-OPS-22071"]), 1)
        self.assertEqual(deps["CENTRAL-OPS-22071"][0]["depends_on_task_id"], "CENTRAL-OPS-22070")


# ===========================================================================
# 12. replace_dependencies
# ===========================================================================

class TestReplaceDependencies(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22080")
        self.create_task("CENTRAL-OPS-22081")
        self.create_task("CENTRAL-OPS-22082")

    def test_replace_dependencies_sets_new_deps(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.replace_dependencies(
                    conn,
                    "CENTRAL-OPS-22080",
                    ["CENTRAL-OPS-22081", "CENTRAL-OPS-22082"],
                )
            deps = task_db.load_dependencies(conn, ["CENTRAL-OPS-22080"])
        finally:
            conn.close()
        self.assertEqual(len(deps["CENTRAL-OPS-22080"]), 2)

    def test_replace_dependencies_clears_existing(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.replace_dependencies(conn, "CENTRAL-OPS-22080", ["CENTRAL-OPS-22081"])
            with conn:
                task_db.replace_dependencies(conn, "CENTRAL-OPS-22080", [])
            deps = task_db.load_dependencies(conn, ["CENTRAL-OPS-22080"])
        finally:
            conn.close()
        self.assertEqual(deps.get("CENTRAL-OPS-22080", []), [])


# ===========================================================================
# 13. merge_task_metadata
# ===========================================================================

class TestMergeTaskMetadata(unittest.TestCase):

    def test_merge_empty_with_dict(self) -> None:
        result = task_db.merge_task_metadata(None, {"audit_required": False})
        self.assertIn("audit_required", result)

    def test_merge_existing_json_with_update(self) -> None:
        existing = json.dumps({"audit_required": False, "existing_key": "value"})
        result = task_db.merge_task_metadata(existing, {"new_key": "new_value"})
        self.assertEqual(result["existing_key"], "value")
        self.assertEqual(result["new_key"], "new_value")

    def test_merge_with_none_incoming(self) -> None:
        existing = json.dumps({"key": "value"})
        result = task_db.merge_task_metadata(existing, None)
        self.assertEqual(result["key"], "value")


# ===========================================================================
# 14. fetch_task_numbers_for_series
# ===========================================================================

class TestFetchTaskNumbersForSeries(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22090")
        self.create_task("CENTRAL-OPS-22091")

    def test_fetch_task_numbers_returns_set(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            numbers = task_db.fetch_task_numbers_for_series(conn, "CENTRAL-OPS")
        finally:
            conn.close()
        self.assertIsInstance(numbers, set)
        self.assertIn(22090, numbers)
        self.assertIn(22091, numbers)


# ===========================================================================
# 15. fetch_execution_row
# ===========================================================================

class TestFetchExecutionRow(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22100")

    def test_fetch_execution_row_returns_row(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            row = task_db.fetch_execution_row(conn, "CENTRAL-OPS-22100")
        finally:
            conn.close()
        self.assertIsNotNone(row)

    def test_fetch_execution_row_none_for_missing(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            row = task_db.fetch_execution_row(conn, "CENTRAL-OPS-99999")
        finally:
            conn.close()
        self.assertIsNone(row)


# ===========================================================================
# 16. close_active_assignments
# ===========================================================================

class TestCloseActiveAssignments(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22110")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.runtime_claim(
                    conn,
                    worker_id="worker-close",
                    queue_name="default",
                    lease_seconds=300,
                    task_id="CENTRAL-OPS-22110",
                    actor_id="boost.tests",
                )
        finally:
            conn.close()

    def test_close_active_assignments(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.close_active_assignments(
                    conn,
                    task_id="CENTRAL-OPS-22110",
                    assignee_kind="worker",
                    assignee_id="worker-close",
                )
        finally:
            conn.close()
        # Should complete without error


# ===========================================================================
# 17. begin_immediate
# ===========================================================================

class TestBeginImmediate(_BaseDbTest):

    def test_begin_immediate_and_rollback(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            task_db.begin_immediate(conn)
            conn.rollback()
        finally:
            conn.close()


# ===========================================================================
# 18. upsert_execution_settings
# ===========================================================================

class TestUpsertExecutionSettings(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-22120")

    def test_upsert_execution_settings_updates_row(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.upsert_execution_settings(
                    conn,
                    "CENTRAL-OPS-22120",
                    {
                        "task_kind": "mutating",
                        "sandbox_mode": "workspace-write",
                        "approval_policy": "never",
                        "additional_writable_dirs": [],
                        "timeout_seconds": 3600,
                        "metadata": {},
                    },
                )
            row = task_db.fetch_execution_row(conn, "CENTRAL-OPS-22120")
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["task_kind"], "mutating")


# ===========================================================================
# 19. detect_status_mismatch additional cases
# ===========================================================================

class TestDetectStatusMismatchAdditional(unittest.TestCase):

    def test_runtime_done_but_planner_todo(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-1",
            planner_status="todo",
            runtime_status="done",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "runtime_done_planner_not_done")

    def test_pending_review_with_done_planner(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-2",
            planner_status="done",
            runtime_status="pending_review",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "pending_review_planner_done")

    def test_pending_review_with_todo_planner_ok(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-3",
            planner_status="todo",
            runtime_status="pending_review",
        )
        self.assertIsNone(result)


# ===========================================================================
# 20. parse_iso_datetime
# ===========================================================================

class TestParseIsoDatetime(unittest.TestCase):

    def test_parse_valid_datetime(self) -> None:
        result = task_db.parse_iso_datetime("2024-01-15T10:00:00+00:00")
        self.assertIsNotNone(result)

    def test_parse_none_returns_none(self) -> None:
        result = task_db.parse_iso_datetime(None)
        self.assertIsNone(result)

    def test_parse_zulu_suffix(self) -> None:
        result = task_db.parse_iso_datetime("2024-01-15T10:00:00Z")
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
