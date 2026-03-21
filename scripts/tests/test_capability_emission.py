#!/usr/bin/env python3
"""Tests for audit-coupled capability mutation emission and enforcement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

import sys

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db  # type: ignore


def task_payload(
    task_id: str,
    *,
    target_repo_id: str = "CENTRAL",
    target_repo_root: str | None = None,
    audit_required: bool = True,
) -> dict[str, object]:
    repo_root = target_repo_root or str(REPO_ROOT)
    return {
        "task_id": task_id,
        "title": f"{task_id} capability emission test",
        "summary": "Exercise audit-coupled capability mutation handling.",
        "objective_md": "Ship a reusable behavior that should land in the capability registry.",
        "context_md": "Synthetic test task.",
        "scope_md": "Capability emission coverage only.",
        "deliverables_md": "- audited capability mutation handling",
        "acceptance_md": "- registry write is atomic with audit acceptance",
        "testing_md": "- unittest coverage",
        "dispatch_md": "No runtime dispatch.",
        "closeout_md": "Synthetic closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": target_repo_id,
        "target_repo_root": repo_root,
        "approval_required": False,
        "initiative": "capability-emission",
        "metadata": {"audit_required": audit_required},
        "execution": {
            "task_kind": "mutating",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": {},
        },
        "dependencies": [],
    }


def create_mutation_payload() -> dict[str, object]:
    return {
        "action": "create",
        "capability_id": "audit_closeout_capability_registry_sync",
        "name": "Audit closeout capability registry sync",
        "summary": "Accepted audits atomically write verified capability records.",
        "kind": "workflow",
        "scope_kind": "workflow",
        "owning_repo_id": "CENTRAL",
        "affected_repo_ids": ["CENTRAL"],
        "entrypoints": ["scripts/central_task_db.py reconcile_audit_pass"],
        "when_to_use_md": "Use when an accepted audit changes reusable behavior.",
        "do_not_use_for_md": "Do not use for local-only bookkeeping with no reusable behavior change.",
        "evidence_summary_md": "Verified by accepted audit task in unit test.",
        "verification_level": "provisional",
        "metadata": {"source": "unit_test"},
    }


def attach_preflight(
    conn: task_db.sqlite3.Connection,
    payload: dict[str, object],
    *,
    actor_id: str,
) -> dict[str, object]:
    request = task_db.canonicalize_preflight_request(
        {
            "normalized_task_intent": task_db.canonicalize_task_intent(payload),
            "search_scope": {"repo_ids": [str(payload["target_repo_id"])]},
            "request_context": {
                "requested_by": actor_id,
                "request_channel": "task-create",
            },
        }
    )
    response = task_db.build_task_preflight_response(conn, request)
    enriched = dict(payload)
    enriched["preflight"] = {
        "request": request,
        "response": response,
        "preflight_token": response["preflight_token"],
        "classification": response["classification_options"][0],
        "novelty_rationale": "No material overlap detected.",
        "related_task_ids": [],
        "related_capability_ids": [],
    }
    return enriched


class CapabilityEmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_capability_emission_")
        self.db_path = Path(self.tmpdir.name) / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
            task_db.ensure_repo(conn, repo_id="WORKER", repo_root=str(REPO_ROOT / "generated" / "worker"), display_name="WORKER")
            conn.commit()
            task_db.create_task_graph(
                conn,
                attach_preflight(conn, task_payload("CENTRAL-OPS-6701"), actor_id="capability.emission.tests"),
                actor_kind="test",
                actor_id="capability.emission.tests",
            )
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _reopen_conn(self):
        return task_db.connect(self.db_path)

    def _mark_parent_awaiting_audit(self, conn: task_db.sqlite3.Connection, task_id: str) -> None:
        row = task_db.fetch_task_row(conn, task_id)
        assert row is not None
        metadata = task_db.parse_json_text(row["metadata_json"], default={})
        metadata["closeout"] = {
            "outcome": "awaiting_audit",
            "summary": "implementation complete",
            "source": "test",
        }
        conn.execute(
            """
            UPDATE tasks
            SET planner_status = 'awaiting_audit',
                version = ?,
                updated_at = ?,
                metadata_json = ?
            WHERE task_id = ? AND version = ?
            """,
            (
                int(row["version"]) + 1,
                task_db.now_iso(),
                task_db.compact_json(metadata),
                task_id,
                int(row["version"]),
            ),
        )
        conn.commit()

    def test_task_create_auto_registers_draft_capability(self) -> None:
        conn = self._reopen_conn()
        try:
            capability = task_db.fetch_capability_payload(
                conn,
                task_db.derive_task_capability_id("CENTRAL-OPS-6701"),
            )
            self.assertIsNotNone(capability)
            assert capability is not None
            self.assertEqual(capability["status"], task_db.TASK_CAPABILITY_DRAFT_STATUS)
            self.assertEqual(capability["verification_level"], "provisional")
            self.assertEqual(capability["verified_by_task_id"], "CENTRAL-OPS-6701")
            self.assertEqual(capability["owning_repo_id"], "CENTRAL")
            self.assertEqual(capability["source_tasks"][0]["relationship_kind"], "created_by")
            lifecycle = dict((capability.get("metadata") or {}).get("task_capability_lifecycle") or {})
            self.assertEqual(lifecycle.get("stage"), "draft")
        finally:
            conn.close()

    def test_done_reconcile_activates_task_capability_and_enriches_entrypoints(self) -> None:
        conn = self._reopen_conn()
        try:
            task_id = "CENTRAL-OPS-6702"
            task_db.create_task(
                conn,
                task_payload(task_id, audit_required=False),
                actor_kind="test",
                actor_id="capability.emission.tests",
                auto_register_capability=True,
            )
            task_db.insert_artifact(
                conn,
                task_id=task_id,
                artifact_kind="runtime_artifact",
                path_or_uri="logs/runtime.log",
                label="runtime.log",
                metadata={"source": "unit-test"},
            )
            row = task_db.fetch_task_row(conn, task_id)
            assert row is not None
            task_db.reconcile_task(
                conn,
                task_id=task_id,
                expected_version=int(row["version"]),
                outcome="done",
                summary="done",
                notes=None,
                tests=None,
                artifacts=["artifacts/closeout.md"],
                actor_kind="planner",
                actor_id="capability.emission.tests",
            )
            capability = task_db.fetch_capability_payload(conn, task_db.derive_task_capability_id(task_id))
            self.assertIsNotNone(capability)
            assert capability is not None
            self.assertEqual(capability["status"], "active")
            self.assertEqual(capability["verification_level"], "planner_verified")
            self.assertIn("logs/runtime.log", capability["entrypoints"])
            self.assertIn("artifacts/closeout.md", capability["entrypoints"])
            lifecycle = dict((capability.get("metadata") or {}).get("task_capability_lifecycle") or {})
            self.assertEqual(lifecycle.get("stage"), "active")
        finally:
            conn.close()

    def test_skip_preflight_product_repo_skips_capability_registration(self) -> None:
        conn = self._reopen_conn()
        try:
            payload = task_payload(
                "CENTRAL-OPS-6703",
                target_repo_id="WORKER",
                target_repo_root=str(REPO_ROOT / "generated" / "worker"),
                audit_required=False,
            )
            task_db.create_task_graph(
                conn,
                payload,
                actor_kind="test",
                actor_id="capability.emission.tests",
                skip_preflight=True,
            )
            capability = task_db.fetch_capability_payload(
                conn,
                task_db.derive_task_capability_id("CENTRAL-OPS-6703"),
            )
            self.assertIsNone(capability)
        finally:
            conn.close()

    def test_audit_pass_with_valid_capability_mutation_writes_registry(self) -> None:
        conn = self._reopen_conn()
        try:
            self._mark_parent_awaiting_audit(conn, "CENTRAL-OPS-6701")
            audit_id = "CENTRAL-OPS-6701-AUDIT"
            worker_result = {"capability_mutation": create_mutation_payload()}

            task_db.reconcile_audit_pass(
                conn,
                audit_task_id=audit_id,
                summary="audit accepted",
                actor_id="capability.emission.tests",
                worker_result=worker_result,
            )

            capability = task_db.fetch_capability_payload(
                conn,
                "audit_closeout_capability_registry_sync",
            )
            self.assertIsNotNone(capability)
            assert capability is not None
            self.assertEqual(capability["status"], "active")
            self.assertEqual(capability["verification_level"], "audited")
            self.assertEqual(capability["verified_by_task_id"], audit_id)
            self.assertEqual(capability["affected_repo_ids"], ["CENTRAL"])
            self.assertEqual(capability["source_tasks"][0]["relationship_kind"], "created_by")

            audit_snap = task_db.fetch_task_snapshots(conn, task_id=audit_id)[0]
            parent_snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-6701")[0]
            self.assertEqual(audit_snap["planner_status"], "done")
            self.assertEqual(parent_snap["planner_status"], "done")
            task_capability = task_db.fetch_capability_payload(
                conn,
                task_db.derive_task_capability_id("CENTRAL-OPS-6701"),
            )
            self.assertIsNotNone(task_capability)
            assert task_capability is not None
            self.assertEqual(task_capability["status"], "active")

            application_rows = conn.execute(
                "SELECT source_task_id, outcome FROM capability_mutation_applications"
            ).fetchall()
            self.assertEqual(
                [(str(row[0]), str(row[1])) for row in application_rows],
                [(audit_id, "applied")],
            )
        finally:
            conn.close()

    def test_audit_pass_without_required_capability_mutation_is_rejected(self) -> None:
        conn = self._reopen_conn()
        try:
            self._mark_parent_awaiting_audit(conn, "CENTRAL-OPS-6701")
            with self.assertRaises(RuntimeError):
                task_db.reconcile_audit_pass(
                    conn,
                    audit_task_id="CENTRAL-OPS-6701-AUDIT",
                    summary="audit accepted",
                    actor_id="capability.emission.tests",
                    worker_result={
                        "capability_closeout": {
                            "task_type_category": "must_emit",
                            "capability_emission_required": True,
                            "capability_emission_reason": "Reusable workflow changed.",
                            "capability_mutations": [],
                        }
                    },
                )

            audit_snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-6701-AUDIT")[0]
            parent_snap = task_db.fetch_task_snapshots(conn, task_id="CENTRAL-OPS-6701")[0]
            self.assertEqual(audit_snap["planner_status"], "todo")
            self.assertEqual(parent_snap["planner_status"], "awaiting_audit")
            task_capability = task_db.fetch_capability_payload(
                conn,
                task_db.derive_task_capability_id("CENTRAL-OPS-6701"),
            )
            self.assertIsNotNone(task_capability)
            assert task_capability is not None
            self.assertEqual(task_capability["status"], task_db.TASK_CAPABILITY_DRAFT_STATUS)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM capabilities").fetchone()[0],
                1,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
