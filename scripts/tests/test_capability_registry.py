#!/usr/bin/env python3
"""Tests for capability registry schema creation and core CLI CRUD."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "central_task_db.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db  # type: ignore


CAPABILITY_TABLES = {
    "capabilities",
    "capability_affected_repos",
    "capability_source_tasks",
    "capability_events",
    "task_creation_preflight",
    "capability_mutation_applications",
}


def task_payload(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} capability registry test",
        "summary": "Create a source task for capability provenance tests.",
        "objective_md": "Exercise capability registry CRUD.",
        "context_md": "Temporary DB only.",
        "scope_md": "Capability registry bootstrap coverage only.",
        "deliverables_md": "- one source task",
        "acceptance_md": "- foreign-key provenance works",
        "testing_md": "- automated unittest coverage",
        "dispatch_md": "No runtime dispatch.",
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
        "initiative": "capability-registry",
        "metadata": {"test_case": task_id},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": {},
        },
        "dependencies": [],
    }


def capability_payload(task_id: str) -> dict[str, object]:
    return {
        "capability_id": "dispatcher_parked_task_visibility",
        "name": "Dispatcher parked task visibility",
        "summary": "Dispatcher status surfaces parked non-eligible tasks.",
        "status": "active",
        "kind": "reporting_surface",
        "scope_kind": "workflow",
        "owning_repo_id": "CENTRAL",
        "affected_repo_ids": ["CENTRAL", "WORKER"],
        "when_to_use_md": "Use when triaging queue state and non-eligible work.",
        "do_not_use_for_md": "Do not treat as scheduler policy output.",
        "entrypoints": ["scripts/dispatcher_control.py status", "scripts/central_runtime.py status"],
        "keywords": ["dispatcher", "parked", "visibility"],
        "evidence_summary_md": "Seeded from accepted implementation work.",
        "verification_level": "planner_verified",
        "verified_by_task_id": task_id,
        "metadata": {"bootstrap_mode": True, "seed_origin": "unit_test"},
        "source_tasks": [{"task_id": task_id, "relationship_kind": "seeded_from"}],
    }


class CapabilityRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_capability_registry_")
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
                task_db.ensure_repo(conn, repo_id="WORKER", repo_root=str(REPO_ROOT / "generated" / "worker"), display_name="WORKER")
                task_db.create_task(
                    conn,
                    task_payload("CENTRAL-OPS-6500"),
                    actor_kind="test",
                    actor_id="capability.registry.tests",
                )
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), args[0], "--db-path", str(self.db_path), *args[1:]],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"central_task_db {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def write_json(self, name: str, payload: dict[str, object]) -> Path:
        path = self.tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_migration_creates_capability_tables_and_is_idempotent(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            tables = set(task_db.fetch_tables(conn))
            self.assertTrue(CAPABILITY_TABLES.issubset(tables))
            applied, skipped = task_db.apply_migrations(
                conn,
                task_db.load_migrations(task_db.resolve_migrations_dir(None)),
            )
            self.assertEqual(applied, [])
            self.assertGreaterEqual(len(skipped), 8)
        finally:
            conn.close()

    def test_capability_create_list_show_cli(self) -> None:
        payload_path = self.write_json("capability-create.json", capability_payload("CENTRAL-OPS-6500"))

        created = json.loads(
            self.run_cli("capability-create", "--input", str(payload_path), "--json").stdout
        )
        self.assertEqual(created["capability_id"], "dispatcher_parked_task_visibility")
        self.assertEqual(created["affected_repo_ids"], ["CENTRAL", "WORKER"])
        self.assertEqual(created["source_tasks"][0]["relationship_kind"], "seeded_from")
        self.assertEqual(created["events"][0]["event_type"], "capability.created")

        listed = json.loads(self.run_cli("capability-list", "--json").stdout)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["capability_id"], "dispatcher_parked_task_visibility")

        filtered = json.loads(self.run_cli("capability-list", "--repo-id", "worker", "--json").stdout)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["owning_repo_id"], "CENTRAL")

        shown = json.loads(
            self.run_cli(
                "capability-show",
                "--capability-id",
                "dispatcher_parked_task_visibility",
                "--json",
            ).stdout
        )
        self.assertEqual(shown["verified_by_task_id"], "CENTRAL-OPS-6500")
        self.assertEqual(shown["metadata"]["seed_origin"], "unit_test")

        conn = sqlite3.connect(str(self.db_path))
        try:
            affected_rows = conn.execute(
                "SELECT repo_id FROM capability_affected_repos ORDER BY repo_id ASC"
            ).fetchall()
            source_rows = conn.execute(
                "SELECT task_id, relationship_kind FROM capability_source_tasks"
            ).fetchall()
            event_rows = conn.execute(
                "SELECT event_type FROM capability_events"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in affected_rows], ["CENTRAL", "WORKER"])
        self.assertEqual(source_rows, [("CENTRAL-OPS-6500", "seeded_from")])
        self.assertEqual(event_rows, [("capability.created",)])


if __name__ == "__main__":
    unittest.main()
