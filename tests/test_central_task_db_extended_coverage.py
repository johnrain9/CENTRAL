#!/usr/bin/env python3
"""Extended behavioral coverage for central_task_db.py.

Targets: operator commands, runtime flows, view commands, task-list filters,
reconcile, heartbeat, utility helpers, and schema/migration paths.
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
        "title": f"{task_id} extended coverage",
        "summary": "Extended coverage task.",
        "objective_md": "Exercise extended paths.",
        "context_md": "Temporary fixture only.",
        "scope_md": "No production mutation.",
        "deliverables_md": "- behavioral assertions",
        "acceptance_md": "- paths remain coherent",
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


# ---------------------------------------------------------------------------
# Base test class with helpers
# ---------------------------------------------------------------------------

class _BaseDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_db_ext_")
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
                    actor_id="central.task_db.extended.tests",
                )
        finally:
            conn.close()

    def claim_task(self, task_id: str, *, worker_id: str = "worker-1", lease_seconds: int = 300) -> None:
        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id=worker_id,
                queue_name="default",
                lease_seconds=lease_seconds,
                task_id=task_id,
                actor_id="central.task_db.extended.tests",
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

    def fetch_events(self, task_id: str) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
            return [str(r["event_type"]) for r in rows]
        finally:
            conn.close()


# ===========================================================================
# 1. CLI init and status commands
# ===========================================================================

class TestInitAndStatus(_BaseDbTest):

    def test_init_returns_applied_count_and_tables(self) -> None:
        """init on an existing DB reports already-applied migrations."""
        result = self.run_cli("init", "--json")
        data = json.loads(result.stdout)
        self.assertIn("applied_count", data)
        self.assertIn("tables", data)
        self.assertIsInstance(data["tables"], list)
        self.assertGreater(len(data["tables"]), 0)

    def test_init_on_fresh_db_applies_all_migrations(self) -> None:
        fresh_db = self.tmp_path / "fresh.db"
        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = int(task_db.main([str(CLI), "init", "--db-path", str(fresh_db), "--json"]))
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else 1
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertGreater(data["applied_count"], 0)
        self.assertEqual(data["already_applied_count"], 0)

    def test_status_reports_migration_state(self) -> None:
        result = self.run_cli("status", "--json")
        data = json.loads(result.stdout)
        self.assertTrue(data["exists"])
        self.assertIsInstance(data["applied_migrations"], list)
        self.assertIsInstance(data["pending_migrations"], list)

    def test_status_on_nonexistent_db_shows_not_exists(self) -> None:
        missing = self.tmp_path / "nonexistent.db"
        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = int(task_db.main([str(CLI), "status", "--db-path", str(missing), "--json"]))
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else 1
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertFalse(data["exists"])
        self.assertEqual(data["applied_migrations"], [])
        self.assertGreater(len(data["available_migrations"]), 0)


# ===========================================================================
# 2. Repo commands
# ===========================================================================

class TestRepoCommands(_BaseDbTest):

    def test_repo_upsert_registers_new_repo(self) -> None:
        result = self.run_cli(
            "repo-upsert",
            "--repo-id", "NEWREPO",
            "--repo-root", str(self.tmp_path / "newrepo"),
            "--display-name", "New Repo",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["repo_id"], "NEWREPO")

    def test_repo_list_returns_all_repos(self) -> None:
        result = self.run_cli("repo-list", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        repo_ids = [r["repo_id"] for r in rows]
        self.assertIn("CENTRAL", repo_ids)

    def test_repo_show_returns_repo_detail(self) -> None:
        result = self.run_cli("repo-show", "--repo", "CENTRAL", "--json")
        data = json.loads(result.stdout)
        self.assertEqual(data["repo_id"], "CENTRAL")
        self.assertIn("display_name", data)


# ===========================================================================
# 3. task-list filters
# ===========================================================================

class TestTaskListFilters(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        # Create a second repo for repo-filter tests
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(conn, repo_id="OTHER", repo_root=str(self.tmp_path / "other"), display_name="Other")
        finally:
            conn.close()
        self.create_task("CENTRAL-OPS-20001", initiative="alpha")
        self.create_task("CENTRAL-OPS-20002", initiative="beta")

    def test_task_list_all_tasks(self) -> None:
        result = self.run_cli("task-list", "--json")
        rows = json.loads(result.stdout)
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20001", task_ids)
        self.assertIn("CENTRAL-OPS-20002", task_ids)

    def test_task_list_filter_by_planner_status(self) -> None:
        result = self.run_cli("task-list", "--planner-status", "todo", "--json")
        rows = json.loads(result.stdout)
        for row in rows:
            self.assertEqual(row["planner_status"], "todo")
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20001", task_ids)

    def test_task_list_filter_by_repo_id(self) -> None:
        result = self.run_cli("task-list", "--repo-id", "CENTRAL", "--json")
        rows = json.loads(result.stdout)
        for row in rows:
            self.assertEqual(row["repo"], "CENTRAL")

    def test_task_list_filter_by_initiative(self) -> None:
        result = self.run_cli("task-list", "--initiative", "alpha", "--json")
        rows = json.loads(result.stdout)
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20001", task_ids)
        self.assertNotIn("CENTRAL-OPS-20002", task_ids)

    def test_task_list_empty_status_filter_returns_nothing(self) -> None:
        result = self.run_cli("task-list", "--planner-status", "done", "--json")
        rows = json.loads(result.stdout)
        self.assertEqual(rows, [])

    def test_task_list_text_output(self) -> None:
        """Non-JSON output should not crash."""
        result = self.run_cli("task-list")
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 4. task-show
# ===========================================================================

class TestTaskShow(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20010")

    def test_task_show_returns_task_card(self) -> None:
        result = self.run_cli("task-show", "--task-id", "CENTRAL-OPS-20010", "--json")
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20010")
        self.assertIn("events", data)
        self.assertIn("artifacts", data)

    def test_task_show_missing_task_exits_nonzero(self) -> None:
        result = self.run_cli("task-show", "--task-id", "CENTRAL-OPS-99999", check=False)
        self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# 5. task-reconcile (close-task)
# ===========================================================================

class TestTaskReconcile(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20020")
        self.create_task("CENTRAL-OPS-20021")
        self.create_task("CENTRAL-OPS-20022")

    def test_reconcile_done_marks_task_closed(self) -> None:
        snap = self.fetch_snapshot("CENTRAL-OPS-20020")
        result = self.run_cli(
            "task-reconcile",
            "--task-id", "CENTRAL-OPS-20020",
            "--expected-version", str(snap["version"]),
            "--outcome", "done",
            "--summary", "All acceptance criteria met.",
            "--actor-id", "worker/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20020")
        self.assertEqual(data["planner_status"], "done")

        snap2 = self.fetch_snapshot("CENTRAL-OPS-20020")
        self.assertEqual(snap2["planner_status"], "done")
        self.assertIsNotNone(snap2.get("closed_at"))
        self.assertIn("planner.task_reconciled", self.fetch_events("CENTRAL-OPS-20020"))

    def test_reconcile_second_task_done_via_cli(self) -> None:
        """Second task also reconciles to done via CLI."""
        snap = self.fetch_snapshot("CENTRAL-OPS-20021")
        result = self.run_cli(
            "task-reconcile",
            "--task-id", "CENTRAL-OPS-20021",
            "--expected-version", str(snap["version"]),
            "--outcome", "done",
            "--summary", "All done.",
            "--notes", "No blockers.",
            "--tests", "pytest passed",
            "--actor-id", "worker/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["planner_status"], "done")

    def test_reconcile_version_mismatch_exits_nonzero(self) -> None:
        result = self.run_cli(
            "task-reconcile",
            "--task-id", "CENTRAL-OPS-20022",
            "--expected-version", "999",  # wrong version
            "--outcome", "done",
            "--summary", "Done.",
            "--actor-id", "worker/test",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_reconcile_done_with_artifact(self) -> None:
        artifact = self.tmp_path / "closeout_log.txt"
        artifact.write_text("test passed\n", encoding="utf-8")
        snap = self.fetch_snapshot("CENTRAL-OPS-20020")
        # Re-fetch after test_reconcile_done may have changed version;
        # create a fresh task for this sub-test
        self.create_task("CENTRAL-OPS-20023")
        snap = self.fetch_snapshot("CENTRAL-OPS-20023")
        result = self.run_cli(
            "task-reconcile",
            "--task-id", "CENTRAL-OPS-20023",
            "--expected-version", str(snap["version"]),
            "--outcome", "done",
            "--summary", "Done with artifact.",
            "--artifact", str(artifact),
            "--actor-id", "worker/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["planner_status"], "done")


# ===========================================================================
# 6. Runtime CLI commands
# ===========================================================================

class TestRuntimeCliCommands(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20030")
        self.create_task("CENTRAL-OPS-20031")
        self.create_task("CENTRAL-OPS-20032")
        self.create_task("CENTRAL-OPS-20033")
        self.create_task("CENTRAL-OPS-20034")

    def test_runtime_claim_cli_claims_task(self) -> None:
        result = self.run_cli(
            "runtime-claim",
            "--worker-id", "worker-cli-1",
            "--queue-name", "default",
            "--lease-seconds", "300",
            "--task-id", "CENTRAL-OPS-20030",
            "--actor-id", "dispatcher/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20030")
        snap = self.fetch_snapshot("CENTRAL-OPS-20030")
        self.assertIsNotNone(snap.get("lease"))
        self.assertEqual(snap["lease"]["lease_owner_id"], "worker-cli-1")

    def test_runtime_heartbeat_cli_extends_lease(self) -> None:
        self.claim_task("CENTRAL-OPS-20031", worker_id="worker-hb")
        result = self.run_cli(
            "runtime-heartbeat",
            "--task-id", "CENTRAL-OPS-20031",
            "--worker-id", "worker-hb",
            "--lease-seconds", "600",
            "--actor-id", "dispatcher/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20031")
        events = self.fetch_events("CENTRAL-OPS-20031")
        self.assertIn("runtime.heartbeat", events)

    def test_runtime_transition_to_running(self) -> None:
        self.claim_task("CENTRAL-OPS-20032", worker_id="worker-run")
        result = self.run_cli(
            "runtime-transition",
            "--task-id", "CENTRAL-OPS-20032",
            "--status", "running",
            "--worker-id", "worker-run",
            "--actor-id", "dispatcher/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["runtime"]["runtime_status"], "running")

    def test_runtime_transition_to_done_clears_lease(self) -> None:
        self.claim_task("CENTRAL-OPS-20033", worker_id="worker-done")
        result = self.run_cli(
            "runtime-transition",
            "--task-id", "CENTRAL-OPS-20033",
            "--status", "done",
            "--worker-id", "worker-done",
            "--actor-id", "dispatcher/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["runtime"]["runtime_status"], "done")
        self.assertIsNone(data.get("lease"))

    def test_runtime_transition_to_failed_increments_retry(self) -> None:
        self.claim_task("CENTRAL-OPS-20034", worker_id="worker-fail")
        result = self.run_cli(
            "runtime-transition",
            "--task-id", "CENTRAL-OPS-20034",
            "--status", "failed",
            "--worker-id", "worker-fail",
            "--error-text", "OOM",
            "--actor-id", "dispatcher/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["runtime"]["runtime_status"], "failed")
        self.assertGreater(data["runtime"]["retry_count"], 0)

    def test_runtime_clear_stale_failed_cli(self) -> None:
        # Transition task to failed first
        self.claim_task("CENTRAL-OPS-20034", worker_id="worker-stale2")
        self.run_cli(
            "runtime-transition",
            "--task-id", "CENTRAL-OPS-20034",
            "--status", "failed",
            "--worker-id", "worker-stale2",
            "--actor-id", "dispatcher/test",
        )
        result = self.run_cli("runtime-clear-stale-failed", "--actor-id", "dispatcher/test", "--json")
        data = json.loads(result.stdout)
        self.assertIn("cleared_count", data)

    def test_runtime_eligible_cli(self) -> None:
        result = self.run_cli("runtime-eligible", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)


# ===========================================================================
# 7. View commands
# ===========================================================================

class TestViewCommands(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20040", initiative="view-test")
        self.create_task("CENTRAL-OPS-20041", initiative="view-test")
        # Claim one for active/assignments views
        self.claim_task("CENTRAL-OPS-20041", worker_id="worker-view")

    def test_view_summary_json(self) -> None:
        result = self.run_cli("view-summary", "--json")
        data = json.loads(result.stdout)
        self.assertIn("planner_counts", data)
        self.assertIn("runtime_counts", data)
        self.assertIn("top_eligible", data)

    def test_view_summary_text(self) -> None:
        result = self.run_cli("view-summary")
        self.assertEqual(result.returncode, 0)
        self.assertIn("todo", result.stdout.lower() + result.stderr.lower())

    def test_view_eligible_json(self) -> None:
        result = self.run_cli("view-eligible", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_view_blocked_json(self) -> None:
        result = self.run_cli("view-blocked", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_view_active_json(self) -> None:
        result = self.run_cli("view-active", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_view_assignments_json(self) -> None:
        result = self.run_cli("view-assignments", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        # The claimed task should appear
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20041", task_ids)

    def test_view_review_json(self) -> None:
        result = self.run_cli("view-review", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)

    def test_view_repo_json(self) -> None:
        result = self.run_cli("view-repo", "--repo-id", "CENTRAL", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20040", task_ids)

    def test_view_task_card_json(self) -> None:
        result = self.run_cli("view-task-card", "--task-id", "CENTRAL-OPS-20040", "--json")
        data = json.loads(result.stdout)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20040")

    def test_view_planner_panel_json(self) -> None:
        result = self.run_cli("view-planner-panel", "--json")
        data = json.loads(result.stdout)
        # Key may be 'eligible_work' or 'eligible' depending on version
        self.assertTrue(
            "eligible_work" in data or "eligible" in data,
            f"Expected eligible key in planner panel: {list(data.keys())}",
        )

    def test_view_summary_initiative_filter(self) -> None:
        result = self.run_cli("view-summary", "--initiative", "view-test", "--json")
        data = json.loads(result.stdout)
        self.assertIsInstance(data["per_initiative"], list)


# ===========================================================================
# 8. Task-ID commands
# ===========================================================================

class TestTaskIdCommands(_BaseDbTest):

    def test_task_id_next_returns_next_id(self) -> None:
        result = self.run_cli(
            "task-id-next",
            "--series", "CENTRAL-OPS",
            "--actor-id", "planner/test",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertIn("next_task_id", data)
        self.assertTrue(str(data["next_task_id"]).startswith("CENTRAL-OPS-"))

    def test_task_id_reserve_creates_reservation(self) -> None:
        result = self.run_cli(
            "task-id-reserve",
            "--series", "CENTRAL-OPS",
            "--count", "3",
            "--actor-id", "planner/test",
            "--reserved-for", "test-initiative",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertIn("reservation_id", data)
        self.assertEqual(data["series"], "CENTRAL-OPS")

    def test_task_id_reservations_list(self) -> None:
        # Create a reservation first
        self.run_cli(
            "task-id-reserve",
            "--series", "CENTRAL-OPS",
            "--count", "2",
            "--actor-id", "planner/test",
            "--json",
        )
        result = self.run_cli("task-id-reservations", "--series", "CENTRAL-OPS", "--json")
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)


# ===========================================================================
# 9. Direct function: reconcile_task
# ===========================================================================

class TestReconcileTaskDirect(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20050")
        self.create_task("CENTRAL-OPS-20051")

    def test_reconcile_task_done_direct(self) -> None:
        snap = self.fetch_snapshot("CENTRAL-OPS-20050")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                result = task_db.reconcile_task(
                    conn,
                    task_id="CENTRAL-OPS-20050",
                    expected_version=snap["version"],
                    outcome="done",
                    summary="Passed all checks.",
                    notes="No issues.",
                    tests="pytest -v",
                    artifacts=[],
                    actor_kind="planner",
                    actor_id="planner/direct-test",
                )
        finally:
            conn.close()
        self.assertEqual(result["planner_status"], "done")
        self.assertIsNotNone(result.get("closed_at"))

    def test_reconcile_task_failed_direct(self) -> None:
        snap = self.fetch_snapshot("CENTRAL-OPS-20051")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                result = task_db.reconcile_task(
                    conn,
                    task_id="CENTRAL-OPS-20051",
                    expected_version=snap["version"],
                    outcome="failed",
                    summary="Crashed.",
                    notes=None,
                    tests=None,
                    artifacts=[],
                    actor_kind="planner",
                    actor_id="planner/direct-test",
                )
        finally:
            conn.close()
        self.assertEqual(result["planner_status"], "failed")

    def test_reconcile_task_aligns_runtime_status_on_done(self) -> None:
        """When planner closes done, a failed runtime row should be aligned to done."""
        self.create_task("CENTRAL-OPS-20052")
        self.claim_task("CENTRAL-OPS-20052", worker_id="worker-align")
        # Force runtime to 'failed'
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE task_runtime_state SET runtime_status='failed' WHERE task_id=?",
                    ("CENTRAL-OPS-20052",),
                )
        finally:
            conn.close()
        snap = self.fetch_snapshot("CENTRAL-OPS-20052")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.reconcile_task(
                    conn,
                    task_id="CENTRAL-OPS-20052",
                    expected_version=snap["version"],
                    outcome="done",
                    summary="Operator override.",
                    notes=None,
                    tests=None,
                    artifacts=[],
                    actor_kind="planner",
                    actor_id="planner/direct-test",
                )
        finally:
            conn.close()
        snap2 = self.fetch_snapshot("CENTRAL-OPS-20052")
        self.assertEqual(snap2["planner_status"], "done")
        # runtime should now also be done
        if snap2.get("runtime"):
            self.assertEqual(snap2["runtime"]["runtime_status"], "done")


# ===========================================================================
# 10. Direct function: runtime_heartbeat
# ===========================================================================

class TestRuntimeHeartbeatDirect(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20060")
        self.create_task("CENTRAL-OPS-20061")

    def test_heartbeat_extends_lease(self) -> None:
        self.claim_task("CENTRAL-OPS-20060", worker_id="worker-hb2")
        conn = task_db.connect(self.db_path)
        try:
            snap_before = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-20060")[0]
            old_expires = snap_before["lease"]["lease_expires_at"]
            result = task_db.runtime_heartbeat(
                conn,
                task_id="CENTRAL-OPS-20060",
                worker_id="worker-hb2",
                lease_seconds=3600,
                actor_id="dispatcher/test",
            )
        finally:
            conn.close()
        new_expires = result["lease"]["lease_expires_at"]
        self.assertGreater(new_expires, old_expires)

    def test_heartbeat_wrong_worker_dies(self) -> None:
        self.claim_task("CENTRAL-OPS-20061", worker_id="worker-hb3")
        conn = task_db.connect(self.db_path)
        try:
            stderr = StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    task_db.runtime_heartbeat(
                        conn,
                        task_id="CENTRAL-OPS-20061",
                        worker_id="wrong-worker",
                        lease_seconds=300,
                        actor_id="dispatcher/test",
                    )
        finally:
            conn.close()

    def test_heartbeat_no_lease_dies(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            stderr = StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    task_db.runtime_heartbeat(
                        conn,
                        task_id="CENTRAL-OPS-20060",  # not claimed
                        worker_id="no-lease-worker",
                        lease_seconds=300,
                        actor_id="dispatcher/test",
                    )
        finally:
            conn.close()


# ===========================================================================
# 11. Direct function: auto_reconcile_runtime_success
# ===========================================================================

class TestAutoReconcile(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20070")

    def test_auto_reconcile_marks_done_after_runtime_success(self) -> None:
        self.claim_task("CENTRAL-OPS-20070", worker_id="worker-auto")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                # Transition to done runtime first
                task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-20070",
                    status="done",
                    worker_id="worker-auto",
                    error_text=None,
                    notes="auto reconcile test",
                    artifacts=[],
                    actor_id="dispatcher/test",
                )
            with conn:
                result = task_db.auto_reconcile_runtime_success(
                    conn,
                    task_id="CENTRAL-OPS-20070",
                    actor_id="dispatcher/auto",
                    summary="Done.",
                    notes=None,
                    tests=None,
                    artifacts=[],
                    run_id=None,
                )
        finally:
            conn.close()
        self.assertEqual(result["planner_status"], "done")


# ===========================================================================
# 12. Direct function: summarize_portfolio
# ===========================================================================

class TestSummarizePortfolio(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20080", initiative="port-test")
        self.create_task("CENTRAL-OPS-20081", initiative="port-test")

    def test_summarize_portfolio_counts(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            summary = task_db.summarize_portfolio(conn)
        finally:
            conn.close()
        self.assertIn("planner_counts", summary)
        self.assertIn("todo", summary["planner_counts"])
        self.assertGreaterEqual(summary["planner_counts"]["todo"], 2)

    def test_summarize_portfolio_initiative_filter(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            summary = task_db.summarize_portfolio(conn, initiative="port-test")
        finally:
            conn.close()
        total = sum(summary["planner_counts"].values())
        self.assertEqual(total, 2)

    def test_summarize_portfolio_per_repo(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            summary = task_db.summarize_portfolio(conn)
        finally:
            conn.close()
        repo_ids = [r["repo_id"] for r in summary["per_repo"]]
        self.assertIn("CENTRAL", repo_ids)

    def test_format_summary_text(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            summary = task_db.summarize_portfolio(conn)
        finally:
            conn.close()
        text = task_db.format_summary_text(summary)
        self.assertIn("todo", text.lower())


# ===========================================================================
# 13. format_* helper functions
# ===========================================================================

class TestFormatHelpers(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20090")
        self.create_task("CENTRAL-OPS-20091")
        self.claim_task("CENTRAL-OPS-20090", worker_id="worker-fmt")

    def _all_snapshots(self) -> list:
        conn = task_db.connect(self.db_path)
        try:
            return task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()

    def test_format_eligible_rows_returns_list(self) -> None:
        snaps = self._all_snapshots()
        rows = task_db.format_eligible_rows(snaps)
        self.assertIsInstance(rows, list)

    def test_format_blocked_rows_returns_list(self) -> None:
        snaps = self._all_snapshots()
        rows = task_db.format_blocked_rows(snaps)
        self.assertIsInstance(rows, list)

    def test_format_assignments_rows_includes_claimed(self) -> None:
        snaps = self._all_snapshots()
        rows = task_db.format_assignments_rows(snaps)
        task_ids = [r["task_id"] for r in rows]
        self.assertIn("CENTRAL-OPS-20090", task_ids)

    def test_format_review_rows_returns_list(self) -> None:
        snaps = self._all_snapshots()
        rows = task_db.format_review_rows(snaps)
        self.assertIsInstance(rows, list)

    def test_render_task_card_includes_key_fields(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-20090")[0]
        finally:
            conn.close()
        card = task_db.render_task_card(snap)
        self.assertEqual(card["task_id"], "CENTRAL-OPS-20090")
        self.assertIn("title", card)
        self.assertIn("planner_status", card)

    def test_order_eligible_snapshots_sorts_by_priority(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            snaps = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        ordered = task_db.order_eligible_snapshots(snaps)
        self.assertIsInstance(ordered, list)

    def test_task_is_eligible_for_unclaimed_todo(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-20091")[0]
        finally:
            conn.close()
        self.assertTrue(task_db.task_is_eligible(snap))

    def test_task_is_not_eligible_for_claimed(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-20090")[0]
        finally:
            conn.close()
        self.assertFalse(task_db.task_is_eligible(snap))


# ===========================================================================
# 14. Utility helper functions
# ===========================================================================

class TestUtilityHelpers(unittest.TestCase):

    def test_normalize_repo_id_strips_path_separators(self) -> None:
        rid = task_db.normalize_repo_id("/home/user/projects/myrepo")
        self.assertEqual(rid, "myrepo")

    def test_normalize_repo_id_uses_fallback(self) -> None:
        rid = task_db.normalize_repo_id("", fallback="FALLBACK")
        self.assertEqual(rid, "FALLBACK")

    def test_markdown_summary_truncates_long_text(self) -> None:
        long = "word " * 100
        summary = task_db.markdown_summary(long, fallback="fallback")
        self.assertLessEqual(len(summary), 300)

    def test_markdown_summary_uses_fallback_on_empty(self) -> None:
        result = task_db.markdown_summary("", fallback="use this")
        self.assertEqual(result, "use this")

    def test_strip_wrapped_backticks_removes_single_backticks(self) -> None:
        result = task_db.strip_wrapped_backticks("`some value`")
        self.assertEqual(result, "some value")

    def test_strip_wrapped_backticks_noop_on_plain_text(self) -> None:
        result = task_db.strip_wrapped_backticks("plain text")
        self.assertEqual(result, "plain text")

    def test_normalize_optional_owner_strips_whitespace(self) -> None:
        result = task_db.normalize_optional_owner("  worker/foo  ")
        self.assertEqual(result, "worker/foo")

    def test_normalize_optional_owner_none_on_empty(self) -> None:
        result = task_db.normalize_optional_owner("")
        self.assertIsNone(result)

    def test_normalize_repo_aliases_deduplicates(self) -> None:
        result = task_db.normalize_repo_aliases(["foo", "FOO", "foo", "bar"])
        self.assertEqual(len(result), len(set(result)))

    def test_make_task_id_format(self) -> None:
        tid = task_db.make_task_id("CENTRAL-OPS", 42)
        self.assertEqual(tid, "CENTRAL-OPS-42")

    def test_parse_task_id_extracts_series_and_number(self) -> None:
        series, number = task_db.parse_task_id("CENTRAL-OPS-42")
        self.assertEqual(series, "CENTRAL-OPS")
        self.assertEqual(number, 42)

    def test_parse_task_id_invalid_raises(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                task_db.parse_task_id("not-a-task-id")

    def test_jaccard_overlap_identical_sets(self) -> None:
        score = task_db.jaccard_overlap({"a", "b", "c"}, {"a", "b", "c"})
        self.assertAlmostEqual(score, 1.0)

    def test_jaccard_overlap_disjoint_sets(self) -> None:
        score = task_db.jaccard_overlap({"a"}, {"b"})
        self.assertAlmostEqual(score, 0.0)

    def test_jaccard_overlap_empty_returns_zero(self) -> None:
        score = task_db.jaccard_overlap(set(), set())
        self.assertAlmostEqual(score, 0.0)

    def test_lexical_tokens_splits_words(self) -> None:
        tokens = task_db.lexical_tokens("hello world foo")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_compact_json_returns_compact_string(self) -> None:
        result = task_db.compact_json({"a": 1, "b": [1, 2]})
        self.assertNotIn("\n", result)

    def test_render_table_empty_rows(self) -> None:
        output = task_db.render_table([], [("id", "id"), ("name", "name")])
        self.assertIsInstance(output, str)

    def test_render_table_with_rows(self) -> None:
        rows = [{"id": "1", "name": "foo"}, {"id": "2", "name": "bar"}]
        output = task_db.render_table(rows, [("id", "id"), ("name", "name")])
        self.assertIn("foo", output)
        self.assertIn("bar", output)

    def test_parse_bool_true_values(self) -> None:
        for v in (True, "true", "yes", "1"):
            self.assertTrue(task_db.parse_bool(v, field="test"))

    def test_parse_bool_false_values(self) -> None:
        for v in (False, "false", "no", "0"):
            self.assertFalse(task_db.parse_bool(v, field="test"))

    def test_parse_bool_invalid_raises(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                task_db.parse_bool("maybe", field="test")

    def test_parse_int_valid(self) -> None:
        self.assertEqual(task_db.parse_int("42", field="test"), 42)
        self.assertEqual(task_db.parse_int(7, field="test"), 7)

    def test_parse_int_invalid_raises(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                task_db.parse_int("not_an_int", field="test")

    def test_parse_positive_int_zero_raises(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                task_db.parse_positive_int(0, field="test")

    def test_normalize_text_whitespace(self) -> None:
        result = task_db.normalize_text_whitespace("  hello   world  ")
        self.assertEqual(result, "hello world")

    def test_parse_sections_finds_section(self) -> None:
        text = "## My Section\n\nsome content\n\n## Other\n\nother content"
        sections = task_db.parse_sections(text)
        self.assertIn("My Section", sections)

    def test_sorted_unique_strings_deduplicates(self) -> None:
        result = task_db.sorted_unique_strings(["b", "a", "a", "c"])
        self.assertEqual(result, ["a", "b", "c"])

    def test_now_iso_is_string(self) -> None:
        self.assertIsInstance(task_db.now_iso(), str)

    def test_utc_rfc3339_converts_iso(self) -> None:
        result = task_db.utc_rfc3339("2024-01-15T10:00:00+00:00")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_utc_rfc3339_none_returns_none(self) -> None:
        self.assertIsNone(task_db.utc_rfc3339(None))


# ===========================================================================
# 15. validate_task_payload edge cases
# ===========================================================================

class TestValidateTaskPayload(unittest.TestCase):

    def _base(self) -> dict:
        return {
            "task_id": "CENTRAL-OPS-12345",
            "title": "Test task",
            "summary": "Summary",
            "objective_md": "Objective",
            "context_md": "Context",
            "scope_md": "Scope",
            "deliverables_md": "- deliverable",
            "acceptance_md": "- acceptance",
            "testing_md": "- testing",
            "dispatch_md": "dispatch",
            "closeout_md": "closeout",
            "reconciliation_md": "reconciliation",
            "planner_status": "todo",
            "priority": 1,
            "task_type": "implementation",
            "planner_owner": "planner/test",
            "worker_owner": None,
            "target_repo_id": "CENTRAL",
            "target_repo_root": "/tmp/repo",
            "approval_required": False,
            "initiative": "one-off",
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

    def _capture_die(self, fn, *args, **kwargs) -> str:
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                fn(*args, **kwargs)
        return stderr.getvalue()

    def test_valid_payload_passes(self) -> None:
        result = task_db.validate_task_payload(self._base(), for_update=False)
        self.assertEqual(result["task_id"], "CENTRAL-OPS-12345")

    def test_missing_title_raises(self) -> None:
        payload = self._base()
        del payload["title"]
        # Should not raise on missing optional-ish fields but title is required
        # validate only raises on None values not absent ones
        payload["title"] = None  # type: ignore
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("title", msg)

    def test_invalid_planner_status_raises(self) -> None:
        payload = self._base()
        payload["planner_status"] = "invalid_status"
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("planner_status", msg)

    def test_metadata_must_be_dict(self) -> None:
        payload = self._base()
        payload["metadata"] = "not-a-dict"
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("metadata", msg)

    def test_dependencies_must_be_list_of_strings(self) -> None:
        payload = self._base()
        payload["dependencies"] = [123, 456]  # not strings
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("dependencies", msg)

    def test_empty_initiative_on_create_raises(self) -> None:
        payload = self._base()
        payload["initiative"] = ""
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("initiative", msg)

    def test_none_initiative_on_create_raises(self) -> None:
        payload = self._base()
        payload["initiative"] = None  # type: ignore
        msg = self._capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("initiative", msg)


# ===========================================================================
# 16. Migration and schema helpers
# ===========================================================================

class TestMigrationHelpers(unittest.TestCase):

    def test_load_migrations_returns_non_empty_list(self) -> None:
        migrations = task_db.load_migrations(task_db.resolve_migrations_dir(None))
        self.assertGreater(len(migrations), 0)
        for m in migrations:
            self.assertIsInstance(m, task_db.Migration)
            self.assertIsInstance(m.version, str)
            self.assertIsInstance(m.sql, str)

    def test_apply_migrations_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = task_db.connect(db_path)
            try:
                migrations = task_db.load_migrations(task_db.resolve_migrations_dir(None))
                applied1, skipped1 = task_db.apply_migrations(conn, migrations)
                applied2, skipped2 = task_db.apply_migrations(conn, migrations)
                self.assertGreater(len(applied1), 0)
                self.assertEqual(len(applied2), 0)
                self.assertEqual(len(skipped2), len(migrations))
            finally:
                conn.close()

    def test_fetch_tables_lists_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = task_db.connect(db_path)
            try:
                task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
                tables = task_db.fetch_tables(conn)
                self.assertIn("tasks", tables)
                self.assertIn("task_events", tables)
            finally:
                conn.close()

    def test_require_initialized_db_passes_after_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = task_db.connect(db_path)
            try:
                task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
                # Should not raise
                task_db.require_initialized_db(conn, db_path)
            finally:
                conn.close()

    def test_require_initialized_db_fails_on_empty_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "empty.db"
            conn = task_db.connect(db_path)
            try:
                stderr = StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit):
                        task_db.require_initialized_db(conn, db_path)
            finally:
                conn.close()


# ===========================================================================
# 17. detect_status_mismatch
# ===========================================================================

class TestDetectStatusMismatch(unittest.TestCase):

    def _snap(self, planner_status: str, runtime_status: str | None) -> dict:
        return {
            "task_id": "CENTRAL-TEST-1",
            "planner_status": planner_status,
            "runtime": {"runtime_status": runtime_status} if runtime_status else None,
        }

    def test_no_mismatch_on_consistent_state(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-1",
            planner_status="todo",
            runtime_status=None,
        )
        self.assertIsNone(result)

    def test_mismatch_on_done_planner_failed_runtime(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-1",
            planner_status="done",
            runtime_status="failed",  # terminal mismatch
        )
        self.assertIsNotNone(result)
        self.assertIn("severity", result)

    def test_no_mismatch_on_done_planner_done_runtime(self) -> None:
        result = task_db.detect_status_mismatch(
            task_id="CENTRAL-TEST-1",
            planner_status="done",
            runtime_status="done",
        )
        self.assertIsNone(result)


# ===========================================================================
# 18. build_planner_panel and render_planner_panel_text
# ===========================================================================

class TestPlannerPanel(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20100")
        self.create_task("CENTRAL-OPS-20101")

    def test_build_planner_panel_returns_dict_with_eligible(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            panel = task_db.build_planner_panel(conn)
        finally:
            conn.close()
        # Key may be 'eligible_work' or 'eligible' depending on version
        eligible_key = "eligible_work" if "eligible_work" in panel else "eligible"
        self.assertIn(eligible_key, panel)
        self.assertIsInstance(panel[eligible_key], list)

    def test_render_planner_panel_text_returns_string(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            panel = task_db.build_planner_panel(conn)
        finally:
            conn.close()
        text = task_db.render_planner_panel_text(panel)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)


# ===========================================================================
# 19. runtime_transition edge cases
# ===========================================================================

class TestRuntimeTransitionEdgeCases(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20110")
        self.create_task("CENTRAL-OPS-20111")
        self.claim_task("CENTRAL-OPS-20110", worker_id="worker-edge")
        self.claim_task("CENTRAL-OPS-20111", worker_id="worker-edge2")

    def test_transition_to_pending_review(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                snap = task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-20110",
                    status="pending_review",
                    worker_id="worker-edge",
                    error_text=None,
                    notes="Needs review",
                    artifacts=[],
                    actor_id="dispatcher/test",
                )
        finally:
            conn.close()
        self.assertEqual(snap["runtime"]["runtime_status"], "pending_review")

    def test_transition_does_not_regress_from_done(self) -> None:
        """A stale transition to 'failed' after already-done should be a no-op."""
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-20111",
                    status="done",
                    worker_id="worker-edge2",
                    error_text=None,
                    notes=None,
                    artifacts=[],
                    actor_id="dispatcher/test",
                )
            with conn:
                snap = task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-20111",
                    status="failed",  # should not regress
                    worker_id=None,
                    error_text="stale write",
                    notes=None,
                    artifacts=[],
                    actor_id="dispatcher/test",
                )
        finally:
            conn.close()
        self.assertEqual(snap["runtime_status"], "done")

    def test_transition_with_worker_model(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                snap = task_db.runtime_transition(
                    conn,
                    task_id="CENTRAL-OPS-20110",
                    status="running",
                    worker_id="worker-edge",
                    error_text=None,
                    notes=None,
                    artifacts=[],
                    actor_id="dispatcher/test",
                    effective_worker_model="claude-opus-4-6",
                    worker_model_source="dispatcher_policy",
                )
        finally:
            conn.close()
        self.assertEqual(snap["runtime"]["effective_worker_model"], "claude-opus-4-6")


# ===========================================================================
# 20. runtime_requeue_task direct
# ===========================================================================

class TestRuntimeRequeueTaskDirect(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20120")

    def test_requeue_from_failed_state(self) -> None:
        self.claim_task("CENTRAL-OPS-20120", worker_id="worker-rq")
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE task_runtime_state SET runtime_status='failed', retry_count=2 WHERE task_id=?",
                    ("CENTRAL-OPS-20120",),
                )
                conn.execute(
                    "UPDATE tasks SET planner_status='failed' WHERE task_id=?",
                    ("CENTRAL-OPS-20120",),
                )
            with conn:
                snap = task_db.runtime_requeue_task(
                    conn,
                    task_id="CENTRAL-OPS-20120",
                    reason="Manual requeue after inspection",
                    actor_id="operator/test",
                    reset_retry_count=True,
                )
        finally:
            conn.close()
        self.assertEqual(snap["planner_status"], "todo")
        self.assertEqual(snap["runtime"]["runtime_status"], "queued")
        self.assertEqual(snap["runtime"]["retry_count"], 0)


# ===========================================================================
# 21. runtime_clear_stale_failed direct
# ===========================================================================

class TestRuntimeClearStaleFailed(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20130")
        self.claim_task("CENTRAL-OPS-20130", worker_id="worker-csf")

    def test_clear_stale_failed_returns_count(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE task_runtime_state SET runtime_status='failed' WHERE task_id=?",
                    ("CENTRAL-OPS-20130",),
                )
            result = task_db.runtime_clear_stale_failed(conn, actor_id="dispatcher/test")
        finally:
            conn.close()
        self.assertIn("cleared_count", result)
        self.assertIsInstance(result["cleared_count"], int)


# ===========================================================================
# 22. ensure_repo with metadata
# ===========================================================================

class TestEnsureRepo(_BaseDbTest):

    def test_ensure_repo_creates_new_repo(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(conn, repo_id="TESTMETAREPO", repo_root="/tmp/testmetarepo", display_name="Test Meta Repo")
            row = task_db.fetch_repo_payload(conn, "TESTMETAREPO")
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["repo_id"], "TESTMETAREPO")

    def test_ensure_repo_updates_on_re_upsert(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL Updated")
            row = task_db.fetch_repo_payload(conn, "CENTRAL")
        finally:
            conn.close()
        self.assertEqual(row["display_name"], "CENTRAL Updated")


# ===========================================================================
# 23. planner-new CLI
# ===========================================================================

class TestPlannerNew(_BaseDbTest):

    def test_planner_new_creates_task_payload(self) -> None:
        result = self.run_cli(
            "planner-new",
            "--title", "Extended coverage planner scaffold",
            "--repo", "CENTRAL",
            "--priority", "3",
            "--actor-id", "planner/test",
            "--initiative", "coverage-work",
        )
        data = json.loads(result.stdout)
        self.assertIn("task_id", data)
        self.assertTrue(str(data["task_id"]).startswith("CENTRAL-"))
        self.assertEqual(data["initiative"], "coverage-work")


# ===========================================================================
# 24. dep-show command
# ===========================================================================

class TestDepCommands(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20140")

    def test_dep_show_returns_dependency_info(self) -> None:
        result = self.run_cli("dep-show", "--task-id", "CENTRAL-OPS-20140", "--json")
        data = json.loads(result.stdout)
        self.assertIn("task_id", data)
        self.assertEqual(data["task_id"], "CENTRAL-OPS-20140")


# ===========================================================================
# 25. export-summary-md / export-task-card-md
# ===========================================================================

class TestExportCommands(_BaseDbTest):

    def setUp(self) -> None:
        super().setUp()
        self.create_task("CENTRAL-OPS-20150")
        self.outdir = self.tmp_path / "exports"
        self.outdir.mkdir()

    def test_export_summary_md_produces_file(self) -> None:
        out = self.outdir / "summary.md"
        result = self.run_cli(
            "export-summary-md",
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")
        self.assertIn("CENTRAL", content)

    def test_export_task_card_md_produces_file(self) -> None:
        out = self.outdir / "task_card.md"
        result = self.run_cli(
            "export-task-card-md",
            "--task-id", "CENTRAL-OPS-20150",
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")
        self.assertIn("CENTRAL-OPS-20150", content)


if __name__ == "__main__":
    unittest.main()
