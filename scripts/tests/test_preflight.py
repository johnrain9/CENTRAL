#!/usr/bin/env python3
"""Tests for task preflight generation and create-path enforcement."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "central_task_db.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db  # type: ignore


def task_payload(task_id: str, *, repo_id: str, repo_root: Path, title: str, summary: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": title,
        "summary": summary,
        "objective_md": f"Implement {title}.",
        "context_md": "Temporary DB only.",
        "scope_md": f"Scope covers {title}.",
        "deliverables_md": f"- implement {title}\n- verify {title}",
        "acceptance_md": f"- {title} works\n- tests pass",
        "testing_md": "- automated unittest coverage",
        "dispatch_md": "No runtime dispatch.",
        "closeout_md": "Synthetic closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": repo_id,
        "target_repo_root": str(repo_root),
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


def build_preflight_request(task: dict[str, object], *, requested_by: str = "planner/coordinator") -> dict[str, object]:
    normalized = task_db.validate_task_payload(task, for_update=False)
    return {
        "normalized_task_intent": task_db.canonicalize_task_intent(normalized),
        "search_scope": {
            "repo_ids": [normalized["target_repo_id"]],
            "include_active_tasks": True,
            "include_recent_done_days": 90,
            "include_capabilities": True,
            "include_deprecated_capabilities": True,
            "max_candidates_per_kind": 50,
        },
        "request_context": {
            "requested_by": requested_by,
            "request_channel": "task-create",
            "is_material_update": False,
            "existing_task_id": None,
            "existing_task_version": None,
        },
    }


def attach_preflight(
    task: dict[str, object],
    request: dict[str, object],
    response: dict[str, object],
    *,
    classification: str | None = None,
    novelty_rationale: str = "This work is materially distinct.",
    related_task_ids: list[str] | None = None,
    related_capability_ids: list[str] | None = None,
) -> dict[str, object]:
    payload = dict(task)
    payload["preflight"] = {
        "request": request,
        "response": response,
        "preflight_token": response["preflight_token"],
        "classification": classification or response["classification_options"][0],
        "novelty_rationale": novelty_rationale,
        "related_task_ids": related_task_ids or [],
        "related_capability_ids": related_capability_ids or [],
    }
    return payload


class PreflightEnforcementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_preflight_")
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
                    task_payload(
                        "CENTRAL-OPS-6500",
                        repo_id="CENTRAL",
                        repo_root=REPO_ROOT,
                        title="Capability registry bootstrap source",
                        summary="Create seed task for capability provenance tests.",
                    ),
                    actor_kind="test",
                    actor_id="preflight.tests",
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

    def capture_preflight_error(self, payload: dict[str, object]) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        stderr = StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    task_db.create_task_graph(conn, payload, actor_kind="planner", actor_id="planner/coordinator")
        finally:
            conn.close()
        return json.loads(stderr.getvalue())

    def test_task_preflight_command_returns_structured_output(self) -> None:
        task = task_payload(
            "CENTRAL-OPS-6601",
            repo_id="WORKER",
            repo_root=REPO_ROOT / "generated" / "worker",
            title="Worker preflight probe",
            summary="Validate task-preflight structured output.",
        )
        request_path = self.write_json("preflight-request.json", build_preflight_request(task))
        response = json.loads(self.run_cli("task-preflight", "--input", str(request_path), "--json").stdout)
        self.assertEqual(response["issued_by"], "CENTRAL")
        self.assertIn("preflight_revision", response)
        self.assertIn("preflight_token", response)
        self.assertIn("classification_options", response)

    def test_task_without_preflight_is_blocked(self) -> None:
        task = task_payload(
            "CENTRAL-OPS-6602",
            repo_id="WORKER",
            repo_root=REPO_ROOT / "generated" / "worker",
            title="Worker task without preflight",
            summary="Create path must reject missing preflight.",
        )
        conn = task_db.connect(self.db_path)
        try:
            request = task_db.canonicalize_preflight_request(build_preflight_request(task))
            response = task_db.build_task_preflight_response(conn, request)
        finally:
            conn.close()
        payload = attach_preflight(task, request, response)
        del payload["preflight"]["preflight_token"]
        del payload["preflight"]["response"]["preflight_token"]
        error = self.capture_preflight_error(payload)
        self.assertEqual(error["error_code"], "preflight_missing")

    def test_task_with_stale_preflight_is_blocked(self) -> None:
        task = task_payload(
            "CENTRAL-OPS-6603",
            repo_id="WORKER",
            repo_root=REPO_ROOT / "generated" / "worker",
            title="Worker stale preflight task",
            summary="Preflight should become stale after scoped writes.",
        )
        conn = task_db.connect(self.db_path)
        try:
            request = task_db.canonicalize_preflight_request(build_preflight_request(task))
            response = task_db.build_task_preflight_response(conn, request)
            task_db.create_task(
                conn,
                task_payload(
                    "CENTRAL-OPS-6604",
                    repo_id="WORKER",
                    repo_root=REPO_ROOT / "generated" / "worker",
                    title="Worker mutation after preflight",
                    summary="Mutate the scoped candidate set.",
                ),
                actor_kind="test",
                actor_id="preflight.tests",
            )
        finally:
            conn.commit()
            conn.close()
        payload = attach_preflight(task, request, response)
        error = self.capture_preflight_error(payload)
        self.assertEqual(error["error_code"], "preflight_stale")

    def test_task_with_fresh_preflight_passes(self) -> None:
        task = task_payload(
            "CENTRAL-OPS-6605",
            repo_id="WORKER",
            repo_root=REPO_ROOT / "generated" / "worker",
            title="Worker fresh preflight task",
            summary="Fresh preflight should allow create.",
        )
        conn = task_db.connect(self.db_path)
        try:
            request = task_db.canonicalize_preflight_request(build_preflight_request(task))
            response = task_db.build_task_preflight_response(conn, request)
            snapshot = task_db.create_task_graph(
                conn,
                attach_preflight(task, request, response, classification="new", novelty_rationale="No matching worker task exists."),
                actor_kind="planner",
                actor_id="planner/coordinator",
            )
            preflight_row = conn.execute(
                "SELECT blocking_bucket, override_kind, novelty_rationale FROM task_creation_preflight WHERE task_id = ?",
                ("CENTRAL-OPS-6605",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(snapshot["task_id"], "CENTRAL-OPS-6605")
        self.assertEqual(preflight_row[0], "none")
        self.assertEqual(preflight_row[1], "none")
        self.assertEqual(preflight_row[2], "No matching worker task exists.")

    def test_override_bypasses_strong_overlap_block(self) -> None:
        existing = task_payload(
            "CENTRAL-OPS-6606",
            repo_id="WORKER",
            repo_root=REPO_ROOT / "generated" / "worker",
            title="Worker overlap baseline",
            summary="Dispatcher overlap baseline task.",
        )
        existing["objective_md"] = "Implement dispatcher overlap detection and enforce novelty gating."
        existing["scope_md"] = "Touch the dispatcher overlap detector and novelty enforcement path."
        existing["deliverables_md"] = "- implement dispatcher overlap detection\n- enforce novelty gating"
        existing["acceptance_md"] = "- dispatcher overlap detection works\n- novelty gating blocks duplicate work"
        conn = task_db.connect(self.db_path)
        try:
            task_db.create_task(conn, existing, actor_kind="test", actor_id="preflight.tests")
            conn.commit()
            task = task_payload(
                "CENTRAL-OPS-6607",
                repo_id="WORKER",
                repo_root=REPO_ROOT / "generated" / "worker",
                title="Worker overlap extension",
                summary="Extend dispatcher novelty gating.",
            )
            task["objective_md"] = existing["objective_md"]
            task["scope_md"] = existing["scope_md"]
            task["deliverables_md"] = existing["deliverables_md"]
            task["acceptance_md"] = existing["acceptance_md"]
            request = task_db.canonicalize_preflight_request(build_preflight_request(task))
            response = task_db.build_task_preflight_response(conn, request)
            self.assertEqual(response["blocking_bucket"], "strong_overlap")
            payload = attach_preflight(
                task,
                request,
                response,
                classification="follow_on",
                novelty_rationale="This task intentionally runs in parallel for a scoped migration split.",
                related_task_ids=["CENTRAL-OPS-6606"],
            )
            payload["override"] = {
                "override_kind": "strong_overlap_privileged",
                "override_reason": "Parallel migration work is intentional.",
                "override_actor_id": "planner/coordinator",
                "override_authority": "planner_admin",
                "acknowledged_candidate_ids": [candidate["candidate_id"] for candidate in response["candidates"]],
                "selected_related_task_ids": ["CENTRAL-OPS-6606"],
                "selected_related_capability_ids": [],
            }
            snapshot = task_db.create_task_graph(
                conn,
                payload,
                actor_kind="planner",
                actor_id="planner/coordinator",
            )
            preflight_row = conn.execute(
                "SELECT blocking_bucket, override_kind, override_reason, metadata_json FROM task_creation_preflight WHERE task_id = ?",
                ("CENTRAL-OPS-6607",),
            ).fetchone()
            task_row = conn.execute(
                "SELECT metadata_json FROM tasks WHERE task_id = ?",
                ("CENTRAL-OPS-6607",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(snapshot["task_id"], "CENTRAL-OPS-6607")
        self.assertEqual(preflight_row[0], "strong_overlap")
        self.assertEqual(preflight_row[1], "strong_overlap_privileged")
        self.assertEqual(preflight_row[2], "Parallel migration work is intentional.")
        self.assertEqual(json.loads(task_row[0])["preflight_override"]["kind"], "strong_overlap_privileged")


if __name__ == "__main__":
    unittest.main()
