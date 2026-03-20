#!/usr/bin/env python3
"""Planner control-panel coverage for CENTRAL."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "central_task_db.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


def task_payload(
    task_id: str,
    *,
    title: str,
    priority: int,
    planner_status: str = "todo",
    dependencies: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": title,
        "summary": f"Summary for {task_id}",
        "objective_md": f"Objective for {task_id}",
        "context_md": f"Context for {task_id}",
        "scope_md": f"Scope for {task_id}",
        "deliverables_md": f"Deliverables for {task_id}",
        "acceptance_md": f"Acceptance for {task_id}",
        "testing_md": f"Testing for {task_id}",
        "dispatch_md": f"Dispatch for {task_id}",
        "closeout_md": f"Closeout for {task_id}",
        "reconciliation_md": f"Reconciliation for {task_id}",
        "planner_status": planner_status,
        "priority": priority,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": {"audit_required": False, **(metadata or {})},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 60,
            "metadata": {},
        },
        "dependencies": dependencies or [],
    }


class PlannerPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_planner_panel_")
        self.db_path = Path(self.tmpdir.name) / "central_tasks.db"
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
                task_db.create_task_graph(
                    conn,
                    task_payload("CENTRAL-ELIGIBLE", title="Eligible task", priority=1),
                    actor_kind="test",
                    actor_id="planner.panel",
                )
                task_db.create_task_graph(
                    conn,
                    task_payload("CENTRAL-BLOCKER", title="Blocking parent", priority=2),
                    actor_kind="test",
                    actor_id="planner.panel",
                )
                task_db.create_task_graph(
                    conn,
                    task_payload(
                        "CENTRAL-PARKED",
                        title="Dependency parked task",
                        priority=3,
                        dependencies=["CENTRAL-BLOCKER"],
                    ),
                    actor_kind="test",
                    actor_id="planner.panel",
                )
                task_db.create_task_graph(
                    conn,
                    task_payload("CENTRAL-STALE", title="Stale task", priority=4),
                    actor_kind="test",
                    actor_id="planner.panel",
                )

                parent = task_db.create_task_graph(
                    conn,
                    task_payload(
                        "CENTRAL-AWAIT",
                        title="Awaiting audit parent",
                        priority=5,
                        metadata={"audit_required": True},
                    ),
                    actor_kind="test",
                    actor_id="planner.panel",
                )
                task_db.reconcile_task(
                    conn,
                    task_id="CENTRAL-AWAIT",
                    expected_version=int(parent["version"]),
                    outcome="awaiting_audit",
                    summary="Implementation finished and ready for audit",
                    notes=None,
                    tests="planner panel seed",
                    artifacts=[],
                    actor_kind="test",
                    actor_id="planner.panel",
                )
            with conn:
                task_db.create_task_graph(
                    conn,
                    task_payload("CENTRAL-FAIL", title="Failing task", priority=6),
                    actor_kind="test",
                    actor_id="planner.panel",
                )
            task_db.runtime_claim(
                conn,
                worker_id="worker-fail",
                queue_name="default",
                lease_seconds=900,
                task_id="CENTRAL-FAIL",
                actor_id="planner.panel",
            )
            task_db.runtime_transition(
                conn,
                task_id="CENTRAL-FAIL",
                status="failed",
                worker_id="worker-fail",
                error_text="unit test failure",
                notes="recent failure for planner panel",
                artifacts=[],
                actor_id="planner.panel",
            )
            self.make_task_stale("CENTRAL-STALE", hours=72)
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def make_task_stale(self, task_id: str, *, hours: int) -> None:
        old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (old_timestamp, task_id))
            conn.commit()
        finally:
            conn.close()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_planner_panel_json_and_text_cover_triage_sections(self) -> None:
        json_result = self.run_cli(
            "view-planner-panel",
            "--db-path",
            str(self.db_path),
            "--stale-hours",
            "24",
            "--changed-since-hours",
            "24",
            "--limit",
            "20",
            "--json",
        )
        payload = json.loads(json_result.stdout)

        self.assertEqual(payload["summary"]["eligible_count"], 5)
        self.assertIn("CENTRAL-ELIGIBLE", [row["task_id"] for row in payload["eligible_work"]])
        self.assertIn("CENTRAL-AWAIT-AUDIT", [row["task_id"] for row in payload["eligible_work"]])
        self.assertEqual(payload["parked_work"]["reason_counts"], {"dependency-blocked": 1})
        self.assertEqual(payload["parked_work"]["rows"][0]["task_id"], "CENTRAL-PARKED")
        self.assertIn("CENTRAL-AWAIT", [row["task_id"] for row in payload["awaiting_audit"]])
        self.assertIn("CENTRAL-STALE", [row["task_id"] for row in payload["stale_or_low_activity"]])
        self.assertIn("CENTRAL-FAIL", [row["task_id"] for row in payload["recent_failures"]])
        self.assertIn("CENTRAL-FAIL", [row["task_id"] for row in payload["changed_since"]])

        text_result = self.run_cli(
            "view-planner-panel",
            "--db-path",
            str(self.db_path),
            "--stale-hours",
            "24",
            "--changed-since-hours",
            "24",
            "--limit",
            "20",
        )
        text = text_result.stdout
        self.assertIn("Planner control panel", text)
        self.assertIn("Eligible work:", text)
        self.assertIn("Parked work:", text)
        self.assertIn("Stale or low activity:", text)
        self.assertIn("Awaiting audit:", text)
        self.assertIn("Recent failures:", text)
        self.assertIn("Changed since:", text)

    def test_repo_scoped_panel_excludes_other_repo_failures_and_change_counts(self) -> None:
        other_root = Path(self.tmpdir.name) / "other_repo"
        other_root.mkdir()
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id="OTHER",
                    repo_root=str(other_root),
                    display_name="OTHER",
                )
                task_db.create_task_graph(
                    conn,
                    task_payload(
                        "OTHER-FAIL",
                        title="Other repo failure",
                        priority=1,
                        metadata={"audit_required": False},
                    )
                    | {
                        "target_repo_id": "OTHER",
                        "target_repo_root": str(other_root),
                    },
                    actor_kind="test",
                    actor_id="planner.panel",
                )
            task_db.runtime_claim(
                conn,
                worker_id="worker-other",
                queue_name="default",
                lease_seconds=900,
                task_id="OTHER-FAIL",
                actor_id="planner.panel",
            )
            task_db.runtime_transition(
                conn,
                task_id="OTHER-FAIL",
                status="failed",
                worker_id="worker-other",
                error_text="other repo failure",
                notes="should stay out of CENTRAL repo view",
                artifacts=[],
                actor_id="planner.panel",
            )
        finally:
            conn.close()

        json_result = self.run_cli(
            "view-planner-panel",
            "--db-path",
            str(self.db_path),
            "--repo-id",
            "CENTRAL",
            "--stale-hours",
            "24",
            "--changed-since-hours",
            "24",
            "--limit",
            "20",
            "--json",
        )
        payload = json.loads(json_result.stdout)

        self.assertEqual(payload["summary"]["recent_failure_count"], 1)
        self.assertEqual(payload["summary"]["changed_since_count"], 7)
        self.assertEqual([row["task_id"] for row in payload["recent_failures"]], ["CENTRAL-FAIL"])
        self.assertNotIn("OTHER-FAIL", [row["task_id"] for row in payload["changed_since"]])


if __name__ == "__main__":
    unittest.main()
