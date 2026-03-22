#!/usr/bin/env python3
"""Behavioral coverage for central_task_db operator, runtime, and bootstrap paths."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
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


def task_payload(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} runtime coverage",
        "summary": "Exercise operator and runtime task DB paths.",
        "objective_md": "Cover operator fail/requeue/cancel and bootstrap flows.",
        "context_md": "Temporary database fixture only.",
        "scope_md": "No production repo mutation.",
        "deliverables_md": "- behavioral assertions",
        "acceptance_md": "- runtime and migration paths remain coherent",
        "testing_md": "- automated unittest coverage only",
        "dispatch_md": f"Dispatch from CENTRAL using repo=CENTRAL do task {task_id}.",
        "closeout_md": "Synthetic closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": "one-off",
        "metadata": {"audit_required": False, "test_case": task_id},
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


class CentralTaskDbOperatorRuntimePathsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_db_operator_runtime_")
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"

        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id="CENTRAL",
                    repo_root=str(REPO_ROOT),
                    display_name="CENTRAL",
                )
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
            code = exc.code if isinstance(exc.code, int) else 1
            returncode = int(code)
        result = CliResult(returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        if check and result.returncode != 0:
            self.fail(f"central_task_db {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def create_task(self, task_id: str) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(conn, task_payload(task_id), actor_kind="test", actor_id="central.task_db.operator_runtime.tests")
        finally:
            conn.close()

    def fetch_snapshot(self, task_id: str) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def fetch_events(self, task_id: str) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
            return [str(row["event_type"]) for row in rows]
        finally:
            conn.close()

    def fetch_artifacts(self, task_id: str) -> list[dict[str, object]]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT artifact_kind, path_or_uri, label
                FROM task_artifacts
                WHERE task_id = ?
                ORDER BY artifact_id ASC
                """,
                (task_id,),
            ).fetchall()
            return [dict(row) for row in rows]
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
                actor_id="central.task_db.operator_runtime.tests",
            )
            self.assertIsNotNone(claim)
        finally:
            conn.close()

    def test_operator_fail_task_cli_marks_task_failed_and_reports_worker_metadata(self) -> None:
        task_id = "CENTRAL-OPS-10601"
        self.create_task(task_id)
        self.claim_task(task_id, worker_id="worker-fail")

        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE task_active_leases
                    SET lease_metadata_json = ?
                    WHERE task_id = ?
                    """,
                    (
                        json.dumps(
                            {
                                "supervision": {
                                    "worker_pid": 4242,
                                    "worker_process_start_token": "token-4242",
                                }
                            }
                        ),
                        task_id,
                    ),
                )
        finally:
            conn.close()

        result = self.run_cli(
            "operator-fail-task",
            "--task-id",
            task_id,
            "--reason",
            "operator terminated stuck worker",
            "--actor-id",
            "operator/test",
            "--json",
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["task_id"], task_id)
        self.assertTrue(payload["kill_target"]["had_active_lease"])
        self.assertEqual(payload["kill_target"]["worker_pid"], 4242)
        self.assertEqual(payload["kill_target"]["worker_process_start_token"], "token-4242")
        self.assertEqual(payload["snapshot"]["planner_status"], "failed")
        self.assertEqual(payload["snapshot"]["runtime"]["runtime_status"], "failed")

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(snapshot["planner_status"], "failed")
        self.assertEqual((snapshot.get("runtime") or {}).get("runtime_status"), "failed")
        self.assertIsNone(snapshot.get("lease"))
        self.assertIn("runtime.operator_stop_requested", self.fetch_events(task_id))
        self.assertIn("planner.operator_stop_reconciled", self.fetch_events(task_id))

    def test_runtime_requeue_task_cli_resets_planner_state_and_retry_count(self) -> None:
        task_id = "CENTRAL-OPS-10602"
        self.create_task(task_id)
        self.claim_task(task_id, worker_id="worker-requeue")

        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE task_runtime_state
                    SET runtime_status = 'failed',
                        retry_count = 4,
                        last_runtime_error = 'worker failed'
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
                conn.execute(
                    "UPDATE tasks SET planner_status = 'failed' WHERE task_id = ?",
                    (task_id,),
                )
        finally:
            conn.close()

        result = self.run_cli(
            "runtime-requeue-task",
            "--task-id",
            task_id,
            "--reason",
            "retry after operator inspection",
            "--reset-retry-count",
            "--actor-id",
            "operator/test",
            "--json",
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(payload["planner_status"], "todo")
        self.assertEqual(payload["runtime"]["runtime_status"], "queued")
        self.assertEqual(payload["runtime"]["retry_count"], 0)
        self.assertIsNone(payload["lease"])

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(snapshot["planner_status"], "todo")
        self.assertEqual((snapshot.get("runtime") or {}).get("runtime_status"), "queued")
        self.assertEqual((snapshot.get("runtime") or {}).get("retry_count"), 0)
        self.assertIsNone(snapshot.get("lease"))
        self.assertIn("runtime.requeued", self.fetch_events(task_id))

    def test_runtime_transition_cli_canceled_clears_lease_and_records_artifact(self) -> None:
        task_id = "CENTRAL-OPS-10603"
        self.create_task(task_id)
        self.claim_task(task_id, worker_id="worker-cancel")
        artifact = self.tmp_path / "cancel-log.txt"
        artifact.write_text("operator canceled the run\n", encoding="utf-8")

        result = self.run_cli(
            "runtime-transition",
            "--task-id",
            task_id,
            "--status",
            "canceled",
            "--worker-id",
            "worker-cancel",
            "--notes",
            "operator canceled queued work",
            "--artifact",
            str(artifact),
            "--actor-id",
            "dispatcher/test",
            "--json",
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(payload["runtime"]["runtime_status"], "canceled")
        self.assertIsNone(payload["lease"])

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual((snapshot.get("runtime") or {}).get("runtime_status"), "canceled")
        self.assertIsNone(snapshot.get("lease"))
        self.assertIn("runtime.status_transition", self.fetch_events(task_id))
        artifacts = self.fetch_artifacts(task_id)
        self.assertTrue(any(row["path_or_uri"] == str(artifact) for row in artifacts))

    def test_runtime_recover_stale_cli_requeues_expired_leases(self) -> None:
        task_id = "CENTRAL-OPS-10604"
        self.create_task(task_id)
        self.claim_task(task_id, worker_id="worker-stale", lease_seconds=300)

        conn = task_db.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE task_active_leases
                    SET lease_expires_at = '2000-01-01T00:00:00+00:00'
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
        finally:
            conn.close()

        result = self.run_cli("runtime-recover-stale", "--limit", "10", "--actor-id", "dispatcher/test", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(payload["recovered_count"], 1)
        self.assertEqual(payload["recovered"][0]["task_id"], task_id)

        snapshot = self.fetch_snapshot(task_id)
        runtime = snapshot.get("runtime") or {}
        self.assertEqual(runtime.get("runtime_status"), "queued")
        self.assertEqual(runtime.get("claimed_by"), None)
        self.assertEqual(runtime.get("retry_count"), 1)
        self.assertIsNone(snapshot.get("lease"))
        self.assertIn("runtime.stale_lease_recovered", self.fetch_events(task_id))

    def test_migrate_bootstrap_cli_imports_skip_and_update_paths(self) -> None:
        task_id = "CENTRAL-OPS-10610"
        packet_task_id = "CENTRAL-OPS-10611"
        tasks_dir = self.tmp_path / "bootstrap_tasks"
        tasks_dir.mkdir()
        packet_path = self.tmp_path / "bootstrap_packet.md"

        task_file = tasks_dir / f"{task_id}.md"
        task_file.write_text(
            textwrap.dedent(
                f"""\
                # {task_id} Bootstrap import test

                ## Task Metadata

                - `Task ID`: `{task_id}`
                - `Status`: `todo`
                - `Target Repo`: `{REPO_ROOT}`
                - `Task Type`: `implementation`
                - `Planner Owner`: `planner/coordinator`
                - `Worker Owner`: `unassigned`
                - `Source Of Truth`: bootstrap snapshot
                - `Summary Record`: [`tasks.md`]({REPO_ROOT / "tasks.md"})

                ## Execution Settings

                - `Priority`: `7`
                - `Task Kind`: `mutating`
                - `Sandbox Mode`: `workspace-write`
                - `Approval Policy`: `never`
                - `Additional Writable Dirs`: `[]`
                - `Timeout Seconds`: `1800`
                - `Approval Required`: `false`

                ## Objective

                Import this task from markdown.

                ## Context

                - Bootstrap task import fixture.

                ## Scope Boundaries

                - Import only.

                ## Deliverables

                1. One imported task row.

                ## Acceptance

                1. The task appears in the DB.

                ## Testing

                - Automated test only.

                ## Dependencies

                - None

                ## Dispatch Contract

                - Dispatch from `CENTRAL` using `repo=CENTRAL do task {task_id}`.

                ## Closeout Contract

                Synthetic closeout.

                ## Repo Reconciliation

                - CENTRAL DB remains canonical.
                """
            ),
            encoding="utf-8",
        )
        packet_path.write_text(
            textwrap.dedent(
                f"""\
                ## Task {packet_task_id}: Packet-only bootstrap test

                ## Repo

                Primary repo: `{REPO_ROOT}`

                ## Status

                `todo`

                ## Objective

                Import this task from the packet only.

                ## Context

                Packet-only fixture.

                ## Deliverables

                - Imported packet task.

                ## Acceptance Criteria

                - The packet task appears in the DB.

                ## Testing

                - Automated test only.

                ## Notes

                Packet notes for migration coverage.
                """
            ),
            encoding="utf-8",
        )

        imported = self.run_cli(
            "migrate-bootstrap",
            "--tasks-dir",
            str(tasks_dir),
            "--packet-path",
            str(packet_path),
            "--actor-id",
            "migration/test",
            "--json",
        )
        imported_payload = json.loads(imported.stdout)
        self.assertEqual(imported_payload["imported_count"], 2)
        self.assertEqual(imported_payload["updated_count"], 0)
        self.assertEqual(imported_payload["skipped_count"], 0)

        skipped = self.run_cli(
            "migrate-bootstrap",
            "--tasks-dir",
            str(tasks_dir),
            "--packet-path",
            str(packet_path),
            "--actor-id",
            "migration/test",
            "--json",
        )
        skipped_payload = json.loads(skipped.stdout)
        self.assertEqual(skipped_payload["imported_count"], 0)
        self.assertEqual(skipped_payload["updated_count"], 0)
        self.assertEqual(skipped_payload["skipped_count"], 2)

        updated_text = task_file.read_text(encoding="utf-8").replace("Import this task from markdown.", "Import this task from markdown with updates.")
        task_file.write_text(updated_text, encoding="utf-8")
        updated = self.run_cli(
            "migrate-bootstrap",
            "--tasks-dir",
            str(tasks_dir),
            "--packet-path",
            str(packet_path),
            "--actor-id",
            "migration/test",
            "--update-existing",
            "--json",
        )
        updated_payload = json.loads(updated.stdout)
        self.assertEqual(updated_payload["imported_count"], 0)
        self.assertEqual(updated_payload["updated_count"], 2)
        self.assertEqual(updated_payload["skipped_count"], 0)

        markdown_snapshot = self.fetch_snapshot(task_id)
        packet_snapshot = self.fetch_snapshot(packet_task_id)
        self.assertIn("markdown with updates", markdown_snapshot["objective_md"])
        self.assertEqual(markdown_snapshot["source_kind"], "bootstrap_markdown")
        self.assertEqual(packet_snapshot["source_kind"], "bootstrap_packet")
        self.assertIn("migration.bootstrap_imported", self.fetch_events(task_id))
        self.assertIn("migration.bootstrap_updated", self.fetch_events(task_id))
        self.assertTrue(any(row["artifact_kind"] == "bootstrap_source" for row in self.fetch_artifacts(task_id)))


if __name__ == "__main__":
    unittest.main()
