#!/usr/bin/env python3
"""Behavior-focused tests for critical central_task_db validation paths."""

from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


def base_payload(task_id: str = "CENTRAL-OPS-12000") -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": "Behavior test task",
        "summary": "Validate DB behavior",
        "objective_md": "Exercise validation paths.",
        "context_md": "Synthetic fixture.",
        "scope_md": "Validation-only flow.",
        "deliverables_md": "- assertions",
        "acceptance_md": "- behavior is enforced",
        "testing_md": "- python -m pytest",
        "dispatch_md": "No dispatch.",
        "closeout_md": "Synthetic closeout.",
        "reconciliation_md": "None.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": "one-off",
        "metadata": {"audit_required": True},
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


class CentralTaskDbBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_db_behavior_")
        self.db_path = Path(self.tmpdir.name) / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
                task_db.ensure_repo(conn, repo_id="WORKER", repo_root=str(REPO_ROOT / "generated" / "worker"), display_name="WORKER")
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def capture_die(self, fn, *args, **kwargs) -> str:
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                fn(*args, **kwargs)
        return stderr.getvalue()

    def create_task(self, payload: dict[str, object]) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(conn, payload, actor_kind="test", actor_id="central.task_db.behavior.tests")
        finally:
            conn.close()

    def test_task_requires_audit_is_strict_and_investigation_safe(self) -> None:
        self.assertFalse(task_db.task_requires_audit(task_type="investigation", source_kind="planner", metadata={}))
        self.assertTrue(task_db.task_requires_audit(task_type="implementation", source_kind="planner", metadata={"audit_required": True}))
        self.assertFalse(task_db.task_requires_audit(task_type="implementation", source_kind="planner", metadata={"audit_required": False}))
        with self.assertRaises(ValueError):
            task_db.task_requires_audit(task_type="implementation", source_kind="planner", metadata={})

    def test_validate_task_payload_requires_non_empty_initiative_on_create(self) -> None:
        payload = base_payload()
        payload["initiative"] = ""
        message = self.capture_die(task_db.validate_task_payload, payload, for_update=False)
        self.assertIn("initiative is required", message)

    def test_canonicalize_preflight_request_enforces_bounds(self) -> None:
        intent = task_db.canonicalize_task_intent(base_payload())
        bad_days = {
            "normalized_task_intent": intent,
            "search_scope": {"repo_ids": ["CENTRAL"], "include_recent_done_days": 10},
            "request_context": {"requested_by": "planner", "request_channel": "task-create"},
        }
        self.assertIn(
            "include_recent_done_days",
            self.capture_die(task_db.canonicalize_preflight_request, bad_days),
        )

        bad_limit = {
            "normalized_task_intent": intent,
            "search_scope": {"repo_ids": ["CENTRAL"], "max_candidates_per_kind": 201},
            "request_context": {"requested_by": "planner", "request_channel": "task-create"},
        }
        self.assertIn(
            "max_candidates_per_kind",
            self.capture_die(task_db.canonicalize_preflight_request, bad_limit),
        )

    def test_canonicalize_preflight_request_requires_existing_task_for_material_updates(self) -> None:
        request = {
            "normalized_task_intent": task_db.canonicalize_task_intent(base_payload()),
            "search_scope": {"repo_ids": ["CENTRAL"]},
            "request_context": {
                "requested_by": "planner",
                "request_channel": "task-update",
                "is_material_update": True,
            },
        }
        message = self.capture_die(task_db.canonicalize_preflight_request, request)
        self.assertIn("existing_task_id", message)

    def test_resolve_task_repo_target_rejects_conflicting_repo_id_and_root(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            normalized = base_payload()
            normalized["target_repo_id"] = "CENTRAL"
            normalized["target_repo_root"] = str(REPO_ROOT / "generated" / "worker")
            message = self.capture_die(task_db.resolve_task_repo_target, conn, normalized)
        finally:
            conn.close()
        self.assertIn("conflicting repo target references", message)

    def test_runtime_claim_respects_default_repo_worker_cap(self) -> None:
        self.create_task(base_payload("CENTRAL-OPS-12010"))
        self.create_task(base_payload("CENTRAL-OPS-12011"))
        self.create_task(base_payload("CENTRAL-OPS-12012"))
        self.create_task(base_payload("CENTRAL-OPS-12013"))

        conn = task_db.connect(self.db_path)
        try:
            for index in range(3):
                claim = task_db.runtime_claim(
                    conn,
                    worker_id=f"worker-{index}",
                    queue_name="default",
                    lease_seconds=120,
                    task_id=None,
                    actor_id="central.task_db.behavior.tests",
                )
                self.assertIsNotNone(claim)

            blocked = task_db.runtime_claim(
                conn,
                worker_id="worker-over",
                queue_name="default",
                lease_seconds=120,
                task_id=None,
                actor_id="central.task_db.behavior.tests",
                raise_on_empty=False,
            )
        finally:
            conn.close()

        self.assertIsNone(blocked)

    def test_runtime_claim_respects_repo_metadata_worker_cap_override(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id="CENTRAL",
                    repo_root=str(REPO_ROOT),
                    display_name="CENTRAL",
                    metadata={task_db.REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY: 1},
                )
        finally:
            conn.close()

        self.create_task(base_payload("CENTRAL-OPS-12020"))
        self.create_task(base_payload("CENTRAL-OPS-12021"))

        conn = task_db.connect(self.db_path)
        try:
            first = task_db.runtime_claim(
                conn,
                worker_id="worker-1",
                queue_name="default",
                lease_seconds=120,
                task_id=None,
                actor_id="central.task_db.behavior.tests",
            )
            self.assertIsNotNone(first)
            blocked = task_db.runtime_claim(
                conn,
                worker_id="worker-2",
                queue_name="default",
                lease_seconds=120,
                task_id="CENTRAL-OPS-12021",
                actor_id="central.task_db.behavior.tests",
                raise_on_empty=False,
            )
        finally:
            conn.close()

        self.assertIsNone(blocked)


if __name__ == "__main__":
    unittest.main()
