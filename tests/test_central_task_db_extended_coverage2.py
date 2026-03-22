#!/usr/bin/env python3
"""Second wave of extended behavioral coverage for central_task_db.py.

Targets: task-create/update CLI, capability commands, snapshot commands,
export commands, dep-graph/dep-lint, render helpers, create_task_graph,
update_task, render_task_card_markdown.
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def _base_payload(task_id: str, *, initiative: str = "one-off", audit_required: bool = False) -> dict:
    return {
        "task_id": task_id,
        "title": f"{task_id} coverage2",
        "summary": "Coverage2 task.",
        "objective_md": "Exercise additional paths.",
        "context_md": "Temporary fixture only.",
        "scope_md": "No production mutation.",
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
        "metadata": {"audit_required": audit_required},
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
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_db_ext2_")
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
                task_db.create_task(
                    conn,
                    _base_payload(task_id, **kwargs),
                    actor_kind="test",
                    actor_id="central.task_db.ext2.tests",
                )
        finally:
            conn.close()

    def claim_task(self, task_id: str, *, worker_id: str = "worker-1") -> None:
        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id=worker_id,
                queue_name="default",
                lease_seconds=300,
                task_id=task_id,
                actor_id="central.task_db.ext2.tests",
            )
            self.assertIsNotNone(claim)
        finally:
            conn.close()

    def fetch_snapshot(self, task_id: str) -> dict:
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snaps), 1)
            return snaps[0]
        finally:
            conn.close()

    def write_json(self, payload: dict) -> Path:
        f = self.tmp_path / f"payload_{id(payload)}.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        return f


# ===========================================================================
# 1. task-create CLI (skip-preflight path)
# ===========================================================================

class TestTaskCreateCli(_BaseDbTest):

    def test_task_create_skip_preflight_creates_task(self) -> None:
        payload = _base_payload("CENTRAL-OPS-21001")
        payload_file = self.write_json(payload)
        result = self.run_cli(
            "task-create",
            "--input", str(payload_file),
            "--skip-preflight",
            "--actor-id", "planner/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-21001")
        snap = self.fetch_snapshot("CENTRAL-OPS-21001")
        self.assertEqual(snap["planner_status"], "todo")

    def test_task_create_template_flag_prints_and_exits(self) -> None:
        result = self.run_cli("task-create", "--template")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("task_id", data)

    def test_task_create_with_audit_required_creates_audit_sibling(self) -> None:
        payload = _base_payload("CENTRAL-OPS-21002", audit_required=True)
        payload_file = self.write_json(payload)
        result = self.run_cli(
            "task-create",
            "--input", str(payload_file),
            "--skip-preflight",
            "--actor-id", "planner/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-21002")
        snap = self.fetch_snapshot("CENTRAL-OPS-21002")
        self.assertIn("child_audit_task_id", snap.get("metadata") or {})


# ===========================================================================
# 2. task-update CLI
# ===========================================================================

class TestTaskUpdateCli(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21010")

    def test_task_update_modifies_title(self) -> None:
        snap = self.fetch_snapshot("CENTRAL-OPS-21010")
        update = {"title": "Updated title via CLI"}
        update_file = self.write_json(update)
        result = self.run_cli(
            "task-update",
            "--task-id", "CENTRAL-OPS-21010",
            "--expected-version", str(snap["version"]),
            "--input", str(update_file),
            "--actor-id", "planner/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-21010")
        self.assertEqual(data["title"], "Updated title via CLI")

    def test_task_update_version_mismatch_fails(self) -> None:
        update = {"title": "Should fail"}
        update_file = self.write_json(update)
        result = self.run_cli(
            "task-update",
            "--task-id", "CENTRAL-OPS-21010",
            "--expected-version", "999",
            "--input", str(update_file),
            "--actor-id", "planner/test",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# 3. Direct: update_task function
# ===========================================================================

class TestUpdateTaskDirect(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21020")
        self.create_task("CENTRAL-OPS-21021")

    def test_update_task_changes_priority(self) -> None:
        snap = self.fetch_snapshot("CENTRAL-OPS-21020")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                result = task_db.update_task(
                    conn,
                    task_id="CENTRAL-OPS-21020",
                    expected_version=snap["version"],
                    payload={"priority": 5},
                    actor_kind="planner",
                    actor_id="planner/direct-test",
                    allow_active_lease=False,
                )
        finally:
            conn.close()
        self.assertEqual(result["priority"], 5)

    def test_update_task_active_lease_blocked_without_flag(self) -> None:
        self.claim_task("CENTRAL-OPS-21021", worker_id="worker-upd")
        snap = self.fetch_snapshot("CENTRAL-OPS-21021")
        conn = task_db.connect(self.db_path)
        try:
            stderr = StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    with conn:
                        task_db.update_task(
                            conn,
                            task_id="CENTRAL-OPS-21021",
                            expected_version=snap["version"],
                            payload={"priority": 3},
                            actor_kind="planner",
                            actor_id="planner/test",
                            allow_active_lease=False,
                        )
        finally:
            conn.close()

    def test_update_task_active_lease_allowed_with_flag(self) -> None:
        self.create_task("CENTRAL-OPS-21022")
        self.claim_task("CENTRAL-OPS-21022", worker_id="worker-upd2")
        snap = self.fetch_snapshot("CENTRAL-OPS-21022")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                result = task_db.update_task(
                    conn,
                    task_id="CENTRAL-OPS-21022",
                    expected_version=snap["version"],
                    payload={"priority": 4},
                    actor_kind="planner",
                    actor_id="planner/test",
                    allow_active_lease=True,
                )
        finally:
            conn.close()
        self.assertEqual(result["priority"], 4)


# ===========================================================================
# 4. Capability commands
# ===========================================================================

class TestCapabilityCommands(_BaseDbTest):

    def _capability_payload(self) -> dict:
        return {
            "capability_id": "test_coverage_capability",
            "name": "Test Coverage Capability",
            "kind": "reporting_surface",
            "scope_kind": "workflow",
            "summary": "A test capability for coverage.",
            "when_to_use_md": "Use during test coverage work.",
            "do_not_use_for_md": "Do not use in production flows.",
            "evidence_summary_md": "Seeded from coverage task.",
            "owning_repo_id": "CENTRAL",
            "affected_repo_ids": ["CENTRAL"],
            "entrypoints": ["scripts/central_task_db.py"],
            "keywords": ["test", "coverage"],
            "source_tasks": [{"task_id": "CENTRAL-OPS-21001", "relationship_kind": "seeded_from"}],
            "verification_level": "planner_verified",
            "verified_by_task_id": "CENTRAL-OPS-21001",
            "status": "proposed",
            "metadata": {},
        }

    def test_capability_list_returns_list(self) -> None:
        result = self.run_cli("capability-list", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_capability_create_template_prints_payload(self) -> None:
        result = self.run_cli("capability-create", "--template")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("capability_id", data)

    def test_capability_create_and_show(self) -> None:
        # Create the task that the capability references
        self.create_task("CENTRAL-OPS-21001")
        payload = self._capability_payload()
        payload_file = self.write_json(payload)
        create_result = self.run_cli(
            "capability-create",
            "--input", str(payload_file),
            "--actor-kind", "planner",
            "--actor-id", "planner/test",
            "--json",
        )
        data = json.loads(create_result.stdout)
        cap_id = data.get("capability_id") or payload["capability_id"]
        self.assertIsNotNone(cap_id)

        show_result = self.run_cli(
            "capability-show",
            "--capability-id", cap_id,
            "--json",
        )
        show_data = json.loads(show_result.stdout)
        self.assertIn("name", show_data)

    def test_capability_list_with_status_filter(self) -> None:
        result = self.run_cli("capability-list", "--status", "proposed", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)


# ===========================================================================
# 5. Snapshot commands
# ===========================================================================

class TestSnapshotCommands(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21030")
        self.durability_dir = self.tmp_path / "durability"
        self.durability_dir.mkdir()

    def test_snapshot_create_produces_manifest(self) -> None:
        result = self.run_cli(
            "snapshot-create",
            "--durability-dir", str(self.durability_dir),
            "--note", "test snapshot",
            "--actor-id", "test/actor",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertIn("snapshot_id", data)
        self.assertIn("task_count", data)
        self.assertGreater(data["task_count"], 0)
        snapshot_dir = Path(data["snapshot_dir"])
        self.assertTrue(snapshot_dir.exists())

    def _run_cli_raw(self, *args: str, check: bool = True) -> CliResult:
        """Run CLI without auto-adding --db-path."""
        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = int(task_db.main([str(CLI), *args]))
        except SystemExit as exc:
            returncode = int(exc.code) if isinstance(exc.code, int) else 1
        result = CliResult(returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        if check and result.returncode != 0:
            self.fail(f"central_task_db {' '.join(args)} failed (rc={result.returncode}): {result.stderr or result.stdout}")
        return result

    def test_snapshot_list_returns_manifests(self) -> None:
        # Create a snapshot first
        self.run_cli(
            "snapshot-create",
            "--durability-dir", str(self.durability_dir),
            "--actor-id", "test/actor",
        )
        result = self._run_cli_raw(
            "snapshot-list",
            "--durability-dir", str(self.durability_dir),
            "--json",
        )
        data = json.loads(result.stdout)
        # Result may be a list or dict with 'snapshots' key
        snapshots = data if isinstance(data, list) else data.get("snapshots", [])
        self.assertGreater(len(snapshots), 0)

    def test_snapshot_restore_reloads_db(self) -> None:
        # Create snapshot, then add a task, then restore
        create_result = self.run_cli(
            "snapshot-create",
            "--durability-dir", str(self.durability_dir),
            "--actor-id", "test/actor",
            "--json",
        )
        snapshot_id = json.loads(create_result.stdout)["snapshot_id"]

        # Add a task after the snapshot
        self.create_task("CENTRAL-OPS-21031")

        restore_db = self.tmp_path / "restored.db"
        result = self.run_cli(
            "snapshot-restore",
            "--durability-dir", str(self.durability_dir),
            "--snapshot-id", snapshot_id,
            "--db-path", str(restore_db),
            "--no-backup-existing",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertIn("snapshot_id", data)


# ===========================================================================
# 6. Export commands
# ===========================================================================

class TestExportCommands2(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21040")
        self.outdir = self.tmp_path / "exports2"
        self.outdir.mkdir()

    def test_export_tasks_board_md_produces_file(self) -> None:
        out = self.outdir / "tasks.md"
        result = self.run_cli("export-tasks-board-md", "--output", str(out))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")
        self.assertIn("CENTRAL", content)

    def test_export_markdown_bundle_produces_directory(self) -> None:
        bundle_dir = self.outdir / "bundle"
        result = self.run_cli("export-markdown-bundle", "--output-dir", str(bundle_dir))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(bundle_dir.exists())
        # Should produce tasks.md among others
        self.assertTrue((bundle_dir / "tasks.md").exists())


# ===========================================================================
# 7. dep-graph and dep-lint
# ===========================================================================

class TestDepGraphAndLint(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21050")
        self.create_task("CENTRAL-OPS-21051")

    def test_dep_graph_empty(self) -> None:
        result = self.run_cli("dep-graph", "--json")
        edges = json.loads(result.stdout)
        self.assertIsInstance(edges, list)

    def test_dep_graph_text(self) -> None:
        result = self.run_cli("dep-graph")
        self.assertEqual(result.returncode, 0)

    def test_dep_lint_no_warnings_returns_zero(self) -> None:
        result = self.run_cli("dep-lint", "--json")
        # No mentions of other task IDs in content → no warnings
        warnings = json.loads(result.stdout)
        self.assertIsInstance(warnings, list)

    def test_dep_lint_text(self) -> None:
        result = self.run_cli("dep-lint")
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 8. view-audits command
# ===========================================================================

class TestViewAudits(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21060")

    def test_view_audits_returns_list(self) -> None:
        result = self.run_cli("view-audits", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_view_audits_failed_section(self) -> None:
        result = self.run_cli("view-audits", "--section", "failed", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)


# ===========================================================================
# 9. render helper functions
# ===========================================================================

class TestRenderHelpers(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21070")

    def _summary(self) -> dict:
        conn = task_db.connect(self.db_path)
        try:
            return task_db.summarize_portfolio(conn)
        finally:
            conn.close()

    def _snapshot(self) -> dict:
        conn = task_db.connect(self.db_path)
        try:
            return task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-21070")[0]
        finally:
            conn.close()

    def test_render_task_card_markdown_returns_string(self) -> None:
        snap = self._snapshot()
        md = task_db.render_task_card_markdown(snap, generated_at=task_db.now_iso())
        self.assertIsInstance(md, str)
        self.assertIn("CENTRAL-OPS-21070", md)

    def test_render_summary_markdown_returns_string(self) -> None:
        summary = self._summary()
        md = task_db.render_summary_markdown(summary)
        self.assertIsInstance(md, str)
        self.assertIn("todo", md.lower())

    def test_render_generated_tasks_board_returns_string(self) -> None:
        summary = self._summary()
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        board = task_db.render_generated_tasks_board(summary, snaps, generated_at=task_db.now_iso())
        self.assertIsInstance(board, str)
        self.assertIn("CENTRAL-OPS-21070", board)

    def test_render_task_card_markdown_with_claimed_task(self) -> None:
        self.claim_task("CENTRAL-OPS-21070", worker_id="worker-card")
        snap = self._snapshot()
        md = task_db.render_task_card_markdown(snap, generated_at=task_db.now_iso())
        self.assertIn("CENTRAL-OPS-21070", md)


# ===========================================================================
# 10. Direct: create_task_graph
# ===========================================================================

class TestCreateTaskGraph(_BaseDbTest):

    def test_create_task_graph_skip_preflight(self) -> None:
        payload = _base_payload("CENTRAL-OPS-21080")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                snap = task_db.create_task_graph(
                    conn,
                    payload,
                    actor_kind="planner",
                    actor_id="planner/test",
                    skip_preflight=True,
                )
        finally:
            conn.close()
        self.assertEqual(snap["task_id"], "CENTRAL-OPS-21080")
        self.assertEqual(snap["planner_status"], "todo")

    def test_create_task_graph_with_audit_creates_sibling(self) -> None:
        payload = _base_payload("CENTRAL-OPS-21081", audit_required=True)
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                snap = task_db.create_task_graph(
                    conn,
                    payload,
                    actor_kind="planner",
                    actor_id="planner/test",
                    skip_preflight=True,
                )
        finally:
            conn.close()
        self.assertEqual(snap["task_id"], "CENTRAL-OPS-21081")
        metadata = snap.get("metadata") or {}
        self.assertIn("child_audit_task_id", metadata)
        audit_id = metadata["child_audit_task_id"]

        # Verify audit sibling was created
        audit_snap = self.fetch_snapshot(audit_id)
        self.assertEqual(audit_snap["task_type"], "audit")


# ===========================================================================
# 11. Health snapshot commands
# ===========================================================================

class TestHealthSnapshotCommands(_BaseDbTest):

    def _write_sample_bundle(self) -> Path:
        """Write a repo-health bundle JSON in the format expected by health-snapshot-write."""
        bundle = {
            "repos": [
                {
                    "repo": {"repo_id": "CENTRAL", "repo_root": str(REPO_ROOT)},
                    "generated_at": task_db.now_iso(),
                    "summary": {"working_status": "passing"},
                    "metrics": {"test_coverage": 55.0},
                }
            ]
        }
        bundle_file = self.tmp_path / "health_bundle.json"
        bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
        return bundle_file

    def test_health_snapshot_write_creates_entry(self) -> None:
        bundle_file = self._write_sample_bundle()
        result = self.run_cli(
            "health-snapshot-write",
            str(bundle_file),
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertIn("written", data)
        self.assertGreater(data["count"], 0)

    def test_health_snapshot_latest_returns_latest(self) -> None:
        bundle_file = self._write_sample_bundle()
        self.run_cli("health-snapshot-write", str(bundle_file))
        result = self.run_cli("health-snapshot-latest", "--repo-id", "CENTRAL", "--json")
        data = json.loads(result.stdout)
        self.assertIsNotNone(data)

    def test_health_snapshot_history_returns_list(self) -> None:
        bundle_file = self._write_sample_bundle()
        self.run_cli("health-snapshot-write", str(bundle_file))
        result = self.run_cli("health-snapshot-history", "--repo-id", "CENTRAL", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)


# ===========================================================================
# 12. Render repo helpers
# ===========================================================================

class TestRenderRepoHelpers(_BaseDbTest):

    def test_render_repo_rows(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            registry = task_db.fetch_repo_registry(conn)
        finally:
            conn.close()
        output = task_db.render_repo_rows(registry)
        self.assertIsInstance(output, str)
        self.assertIn("CENTRAL", output)

    def test_render_repo_detail(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            row = task_db.fetch_repo_payload(conn, "CENTRAL")
        finally:
            conn.close()
        output = task_db.render_repo_detail(row)
        self.assertIsInstance(output, str)
        self.assertIn("CENTRAL", output)


# ===========================================================================
# 13. Additional utility coverage
# ===========================================================================

class TestAdditionalUtilities(unittest.TestCase):

    def test_normalize_task_id_series_default(self) -> None:
        result = task_db.normalize_task_id_series(None)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_normalize_task_id_series_explicit(self) -> None:
        result = task_db.normalize_task_id_series("CENTRAL-OPS")
        self.assertEqual(result, "CENTRAL-OPS")

    def test_now_iso_format(self) -> None:
        ts = task_db.now_iso()
        # Should be parseable as ISO 8601
        from datetime import datetime
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        self.assertIsNotNone(dt)

    def test_markdown_to_plain_text_strips_markdown(self) -> None:
        result = task_db.markdown_to_plain_text("## Header\n\n**bold** text")
        self.assertIn("bold", result)

    def test_canonicalize_task_intent_returns_dict(self) -> None:
        payload = {
            "task_id": "CENTRAL-OPS-12345",
            "title": "Test task",
            "summary": "Summary",
            "objective_md": "Objective",
            "context_md": "Context",
            "scope_md": "Scope",
            "deliverables_md": "Deliverables",
            "acceptance_md": "Acceptance",
            "task_type": "implementation",
            "planner_owner": "planner/test",
            "target_repo_id": "CENTRAL",
            "initiative": "one-off",
            "metadata": {"audit_required": False},
        }
        intent = task_db.canonicalize_task_intent(payload)
        self.assertIsInstance(intent, dict)
        self.assertIn("task_type", intent)
        self.assertIn("title", intent)

    def test_build_repo_onboarding_command(self) -> None:
        result = task_db.build_repo_onboarding_command(
            repo_id="TESTREPO",
            repo_root="/tmp/testrepo",
            display_name="Test Repo",
        )
        self.assertIn("TESTREPO", result)

    def test_normalize_string_list_handles_various_inputs(self) -> None:
        # List of strings
        result = task_db.normalize_string_list(["a", "b", "c"], field="test")
        self.assertEqual(result, ["a", "b", "c"])

    def test_normalize_string_list_non_list_raises(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                task_db.normalize_string_list("not-a-list", field="test")

    def test_candidate_band_rank_known_bands(self) -> None:
        self.assertGreater(
            task_db.candidate_band_rank("exact_duplicate"),
            task_db.candidate_band_rank("related_recent_work"),
        )

    def test_lexical_token_set_returns_set(self) -> None:
        result = task_db.lexical_token_set("hello world hello")
        self.assertIsInstance(result, set)
        self.assertIn("hello", result)
        # No duplicates
        self.assertEqual(len(result), len({"hello", "world"}))

    def test_parse_iso8601_parses_utc(self) -> None:
        dt = task_db.parse_iso8601("2024-01-15T10:00:00+00:00")
        self.assertIsNotNone(dt)

    def test_sorted_unique_strings_empty(self) -> None:
        result = task_db.sorted_unique_strings([])
        self.assertEqual(result, [])

    def test_normalize_repo_lookup_key(self) -> None:
        result = task_db.normalize_repo_lookup_key("  CENTRAL  ")
        self.assertEqual(result, "central")

    def test_normalize_repo_root_key(self) -> None:
        result = task_db.normalize_repo_root_key("/home/user/projects/CENTRAL")
        self.assertIsInstance(result, str)

    def test_shell_join_formats_command(self) -> None:
        result = task_db.shell_join(["python3", "scripts/build.sh", "--flag"])
        self.assertIn("python3", result)

    def test_file_sha256_of_existing_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"test content")
            fname = f.name
        try:
            checksum = task_db.file_sha256(Path(fname))
            self.assertIsInstance(checksum, str)
            self.assertEqual(len(checksum), 64)  # SHA256 hex
        finally:
            Path(fname).unlink()

    def test_stable_sha256_of_dict(self) -> None:
        payload = {"a": 1, "b": "test"}
        checksum = task_db.stable_sha256(payload)
        self.assertIsInstance(checksum, str)
        # Deterministic
        self.assertEqual(checksum, task_db.stable_sha256(payload))

    def test_parse_markdown_key_values_parses_list_items(self) -> None:
        section = "- `Key`: `value1`\n- `Other`: `value2`"
        result = task_db.parse_markdown_key_values(section)
        self.assertIsInstance(result, dict)

    def test_generated_banner_returns_string(self) -> None:
        banner = task_db.generated_banner(task_db.now_iso())
        self.assertIsInstance(banner, str)
        self.assertIn("generated", banner.lower())

    def test_compact_json_handles_nested(self) -> None:
        data = {"a": {"b": [1, 2, 3]}}
        result = task_db.compact_json(data)
        self.assertIsInstance(result, str)
        # Should round-trip
        self.assertEqual(json.loads(result), data)


# ===========================================================================
# 14. fetch helpers
# ===========================================================================

class TestFetchHelpers(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21090")
        self.claim_task("CENTRAL-OPS-21090", worker_id="worker-fetch")

    def test_fetch_active_lease_returns_lease(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            lease = task_db.fetch_active_lease(conn, "CENTRAL-OPS-21090")
        finally:
            conn.close()
        self.assertIsNotNone(lease)
        self.assertEqual(lease["lease_owner_id"], "worker-fetch")

    def test_fetch_active_lease_returns_none_for_unclaimed(self) -> None:
        self.create_task("CENTRAL-OPS-21091")
        conn = task_db.connect(self.db_path)
        try:
            lease = task_db.fetch_active_lease(conn, "CENTRAL-OPS-21091")
        finally:
            conn.close()
        self.assertIsNone(lease)

    def test_fetch_task_row_returns_row(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            row = task_db.fetch_task_row(conn, "CENTRAL-OPS-21090")
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["task_id"], "CENTRAL-OPS-21090")

    def test_fetch_task_row_returns_none_for_missing(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            row = task_db.fetch_task_row(conn, "CENTRAL-OPS-99999")
        finally:
            conn.close()
        self.assertIsNone(row)

    def test_fetch_latest_events_returns_list(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            events = task_db.fetch_latest_events(conn, "CENTRAL-OPS-21090", limit=5)
        finally:
            conn.close()
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)

    def test_fetch_artifacts_returns_empty_initially(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            artifacts = task_db.fetch_artifacts(conn, "CENTRAL-OPS-21090")
        finally:
            conn.close()
        self.assertIsInstance(artifacts, list)

    def test_registered_repo_ids_includes_central(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            ids = task_db.registered_repo_ids(conn)
        finally:
            conn.close()
        self.assertIn("CENTRAL", ids)

    def test_active_repo_worker_counts_returns_dict(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            counts = task_db.active_repo_worker_counts(conn)
        finally:
            conn.close()
        self.assertIsInstance(counts, dict)
        self.assertIn("CENTRAL", counts)

    def test_fetch_repo_registry_returns_list(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            registry = task_db.fetch_repo_registry(conn)
        finally:
            conn.close()
        self.assertIsInstance(registry, list)
        self.assertGreater(len(registry), 0)


# ===========================================================================
# 15. resolve_repo_reference and related
# ===========================================================================

class TestRepoResolution(_BaseDbTest):

    def test_resolve_repo_reference_by_id(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            result = task_db.resolve_repo_reference(conn, "CENTRAL", field="target_repo", allow_missing=False)
        finally:
            conn.close()
        self.assertIsNotNone(result)
        self.assertEqual(result["repo_id"], "CENTRAL")

    def test_resolve_repo_reference_missing_allows_none(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            result = task_db.resolve_repo_reference(conn, "NONEXISTENT", field="target_repo", allow_missing=True)
        finally:
            conn.close()
        self.assertIsNone(result)

    def test_resolve_repo_filter_returns_none_on_empty(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            result = task_db.resolve_repo_filter(conn, None)
        finally:
            conn.close()
        self.assertIsNone(result)

    def test_resolve_repo_filter_returns_id_for_valid_ref(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            result = task_db.resolve_repo_filter(conn, "CENTRAL")
        finally:
            conn.close()
        self.assertEqual(result, "CENTRAL")

    def test_known_repo_ids_summary_returns_string(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            summary = task_db.known_repo_ids_summary(conn)
        finally:
            conn.close()
        self.assertIn("CENTRAL", summary)


# ===========================================================================
# 16. task_preflight CLI template
# ===========================================================================

class TestTaskPreflightCli(_BaseDbTest):

    def test_task_preflight_template_flag(self) -> None:
        result = self.run_cli("task-preflight", "--template")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("normalized_task_intent", data)


# ===========================================================================
# 17. snapshot-related helpers
# ===========================================================================

class TestSnapshotHelpers(unittest.TestCase):

    def test_generate_snapshot_id_returns_string(self) -> None:
        sid = task_db.generate_snapshot_id()
        self.assertIsInstance(sid, str)
        self.assertGreater(len(sid), 0)

    def test_snapshots_root_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = task_db.snapshots_root(Path(tmp))
            self.assertIsInstance(d, Path)

    def test_latest_snapshot_pointer_path_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = task_db.latest_snapshot_pointer_path(Path(tmp))
            self.assertIsInstance(p, Path)

    def test_render_snapshot_rows_returns_list(self) -> None:
        result = task_db.render_snapshot_rows([])
        self.assertEqual(result, [])


# ===========================================================================
# 18. print_or_json / write_output helpers
# ===========================================================================

class TestOutputHelpers(unittest.TestCase):

    def test_print_or_json_json_mode(self) -> None:
        stdout = StringIO()
        data = {"key": "value"}
        with redirect_stdout(stdout):
            rc = task_db.print_or_json(data, as_json=True, formatter=None)
        self.assertEqual(rc, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result, data)

    def test_print_or_json_text_mode_with_formatter(self) -> None:
        stdout = StringIO()
        data = {"key": "value"}
        with redirect_stdout(stdout):
            rc = task_db.print_or_json(data, as_json=False, formatter=lambda d: f"KEY={d['key']}")
        self.assertEqual(rc, 0)
        self.assertIn("KEY=value", stdout.getvalue())

    def test_write_output_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.txt"
            task_db.write_output(path, "test content")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "test content")

    def test_write_json_document_creates_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.json"
            task_db.write_json_document(path, {"a": 1})
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["a"], 1)


# ===========================================================================
# 19. resolve_db_path / resolve_durability_dir / resolve_migrations_dir
# ===========================================================================

class TestResolveHelpers(unittest.TestCase):

    def test_resolve_db_path_default(self) -> None:
        path = task_db.resolve_db_path(None)
        self.assertIsInstance(path, Path)

    def test_resolve_db_path_explicit(self) -> None:
        path = task_db.resolve_db_path("/tmp/test.db")
        # resolve may return the realpath on macOS (/private/tmp)
        self.assertTrue(str(path).endswith("test.db"))

    def test_resolve_migrations_dir_default(self) -> None:
        path = task_db.resolve_migrations_dir(None)
        self.assertIsInstance(path, Path)
        self.assertTrue(path.exists())

    def test_resolve_durability_dir_default(self) -> None:
        path = task_db.resolve_durability_dir(None)
        self.assertIsInstance(path, Path)


# ===========================================================================
# 20. task-id-reservations (coverage for missing lines)
# ===========================================================================

class TestTaskIdReservationsCoverage(_BaseDbTest):

    def test_task_id_reservations_with_all_flag(self) -> None:
        # Create a reservation first
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.reserve_task_id_range(
                    conn,
                    series="CENTRAL-OPS",
                    count=2,
                    reserved_by="planner/test",
                    reserved_for="coverage-test",
                    note="test reservation",
                    reservation_hours=1,
                )
        finally:
            conn.close()
        result = self.run_cli(
            "task-id-reservations",
            "--series", "CENTRAL-OPS",
            "--all",
            "--json",
        )
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)

    def test_reconcile_task_id_reservations(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.reserve_task_id_range(
                    conn,
                    series="CENTRAL-OPS",
                    count=1,
                    reserved_by="planner/test",
                    reserved_for="reconcile-test",
                    note="test",
                    reservation_hours=1,
                )
            result = task_db.reconcile_task_id_reservations(conn, actor_id="planner/test")
        finally:
            conn.close()
        # Result may be dict or list depending on implementation
        self.assertIsNotNone(result)


# ===========================================================================
# 21. runtime_transition additional states
# ===========================================================================

class TestRuntimeTransitionAdditional(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-21100")
        self.claim_task("CENTRAL-OPS-21100", worker_id="worker-timeout")

    def test_transition_to_timeout_increments_retry(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                snap = task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-21100",
                    status="timeout",
                    worker_id="worker-timeout",
                    error_text="Exceeded time limit",
                    notes=None,
                    artifacts=[],
                    actor_id="dispatcher/test",
                )
        finally:
            conn.close()
        self.assertEqual(snap["runtime"]["runtime_status"], "timeout")
        self.assertGreater(snap["runtime"]["retry_count"], 0)


# ===========================================================================
# 22. repo-resolve command
# ===========================================================================

class TestRepoResolveCommand(_BaseDbTest):

    def test_repo_resolve_returns_repo_id(self) -> None:
        result = self.run_cli("repo-resolve", "--repo", "CENTRAL", "--json")
        data = json.loads(result.stdout)
        self.assertIn("repo_id", data)
        self.assertEqual(data["repo_id"], "CENTRAL")


if __name__ == "__main__":
    unittest.main()
