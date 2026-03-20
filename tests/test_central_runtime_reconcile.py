#!/usr/bin/env python3
"""Tests for runtime-driven planner reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime
import central_task_db as task_db


def task_payload(task_id: str, *, approval_required: bool = False, task_type: str = "implementation") -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} runtime reconcile test",
        "summary": "Exercise runtime/planner reconciliation behavior.",
        "objective_md": "Use the dispatcher to complete a synthetic task.",
        "context_md": "Temporary DB only.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- produce a synthetic worker result",
        "acceptance_md": "- runtime and planner state follow the expected invariant",
        "testing_md": "- automated unittest coverage only",
        "dispatch_md": "Dispatch locally through the CENTRAL runtime harness.",
        "closeout_md": "Synthetic runtime closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": task_type,
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": approval_required,
        "metadata": {"test_case": task_id, "audit_required": False},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": {
                "stub_sleep_seconds": 0.1,
                "stub_log_interval_seconds": 0.05,
            },
        },
        "dependencies": [],
    }


class FakeAutonomyRunner:
    @staticmethod
    def load_result_file(result_path: Path, *, task_id: str, run_id: str):
        payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
        return SimpleNamespace(
            status=payload["status"],
            verdict=str(payload.get("verdict") or ""),
            summary=payload["summary"],
            validation=payload["validation"],
            artifacts=payload["artifacts"],
            task_id=task_id,
            run_id=run_id,
        )


class ValidatingAutonomyRunner:
    """Stub autonomy runner that can enforce identifier consistency."""

    def __init__(self, *, enforce_task_id: bool = False):
        self.enforce_task_id = enforce_task_id

    def load_result_file(self, result_path: Path, *, task_id: str, run_id: str):
        payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
        file_task_id = str(payload.get("task_id"))
        if self.enforce_task_id and file_task_id != task_id:
            raise RuntimeError(f"task_id mismatch: payload={file_task_id!r} expected={task_id!r}")
        return SimpleNamespace(
            status=payload["status"],
            verdict=str(payload.get("verdict") or ""),
            summary=payload["summary"],
            validation=payload["validation"],
            artifacts=payload["artifacts"],
            task_id=task_id,
            run_id=run_id,
        )


class CentralRuntimeReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_runtime_reconcile_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        self.state_dir = tmp_path / "runtime_state"
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

    def create_task(self, payload: dict[str, object]) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(conn, payload, actor_kind="test", actor_id="central.runtime.tests")
        finally:
            conn.close()

    def dispatcher(self) -> central_runtime.CentralDispatcher:
        return central_runtime.CentralDispatcher(
            central_runtime.DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="stub",
            )
        )

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

    def _prepare_worker_state(
        self,
        task_id: str,
        run_id: str,
        payload: dict[str, object],
        *,
        runtime_notes: str = "synthetic worker",
    ) -> tuple[central_runtime.CentralDispatcher, central_runtime.ActiveWorker]:
        dispatcher = self.dispatcher()

        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id="central-worker:test:slot",
                queue_name="default",
                lease_seconds=30,
                task_id=task_id,
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()
        self.assertIsNotNone(claim)
        claim = claim or {}
        worker_id = str((claim.get("lease") or {}).get("lease_owner_id"))

        conn = task_db.connect(self.db_path)
        try:
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status="running",
                worker_id=worker_id,
                error_text=None,
                notes=runtime_notes,
                artifacts=[],
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()

        result_path = dispatcher.paths.worker_results_dir / task_id / f"{run_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload), encoding="utf-8")

        prompt_path = dispatcher.paths.worker_prompts_dir / task_id / f"{run_id}.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("synthetic prompt\n", encoding="utf-8")

        log_path = dispatcher.paths.worker_logs_dir / task_id / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("synthetic log\n", encoding="utf-8")

        state = central_runtime.ActiveWorker(
            task=claim,
            worker_id=worker_id,
            run_id=run_id,
            pid=12345,
            proc=None,
            log_handle=None,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=0.0,
            timeout_seconds=30,
        )
        return dispatcher, state

    def _worker_result_payload(
        self,
        *,
        task_id: str,
        run_id: str,
        status: str = "COMPLETED",
        verdict: str = "accepted",
        summary: str = "synthetic result",
        artifacts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        success = status == "COMPLETED"
        return {
            "status": status,
            "verdict": verdict,
            "schema_version": 1,
            "task_id": task_id,
            "run_id": run_id,
            "summary": summary,
            "completed_items": [summary] if success else [],
            "remaining_items": [],
            "decisions": [],
            "discoveries": [],
            "blockers": [],
            "validation": [
                {
                    "name": "synthetic",
                    "passed": success,
                    "notes": summary,
                }
            ],
            "files_changed": [],
            "warnings": [],
            "artifacts": artifacts or [],
        }

    def test_runtime_done_auto_reconciles_planner_done(self) -> None:
        task_id = "CENTRAL-OPS-9330"
        self.create_task(task_payload(task_id))
        dispatcher = self.dispatcher()

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher.run_once(emit_result=False)

        snapshot = self.fetch_snapshot(task_id)
        metadata = snapshot["metadata"]
        closeout = metadata.get("closeout")
        self.assertEqual(snapshot["planner_status"], "done")
        self.assertEqual(snapshot["runtime"]["runtime_status"], "done")
        self.assertIsNone(snapshot["status_mismatch"])
        self.assertIsInstance(closeout, dict)
        self.assertEqual(closeout["source"], "runtime_auto_reconcile")
        self.assertEqual(closeout["outcome"], "done")
        self.assertTrue(closeout["runtime_run_id"])
        self.assertIn("planner.task_auto_reconciled", self.fetch_events(task_id))

    def test_pending_review_task_remains_unreconciled(self) -> None:
        task_id = "CENTRAL-OPS-9331"
        self.create_task(task_payload(task_id, approval_required=True))
        dispatcher = self.dispatcher()

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher.run_once(emit_result=False)

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(snapshot["planner_status"], "todo")
        self.assertEqual(snapshot["runtime"]["runtime_status"], "pending_review")
        self.assertIsNone(snapshot["metadata"].get("closeout"))
        self.assertNotIn("planner.task_auto_reconciled", self.fetch_events(task_id))

    def test_truth_task_success_routes_to_pending_review(self) -> None:
        task_id = "CENTRAL-OPS-9334"
        payload = task_payload(task_id, task_type="truth")
        self.create_task(payload)
        dispatcher = self.dispatcher()

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher.run_once(emit_result=False)

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(snapshot["planner_status"], "todo")
        self.assertEqual(snapshot["runtime"]["runtime_status"], "pending_review")
        self.assertIsNone(snapshot["metadata"].get("closeout"))
        self.assertNotIn("planner.task_auto_reconciled", self.fetch_events(task_id))

    def test_runtime_done_awaiting_audit_is_not_flagged_as_mismatch(self) -> None:
        task_id = "CENTRAL-OPS-9333"
        payload = task_payload(task_id)
        payload["metadata"] = {"test_case": task_id, "audit_required": True}
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task_graph(conn, payload, actor_kind="test", actor_id="central.runtime.tests")
        finally:
            conn.close()
        dispatcher = self.dispatcher()

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher.run_once(emit_result=False)

        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(snapshot["planner_status"], "awaiting_audit")
        self.assertEqual(snapshot["runtime"]["runtime_status"], "done")
        self.assertIsNone(snapshot["status_mismatch"])
        events = self.fetch_events(task_id)
        self.assertNotIn("planner.task_auto_reconcile_failed", events)
        log_path = dispatcher.paths.log_path
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        self.assertNotIn("worker_auto_reconcile_failed", log_text)
        self.assertIn("worker_auto_reconcile_skipped", log_text)

    def test_audit_verdict_rework_required_fails_audit_and_parent(self) -> None:
        task_id = "CENTRAL-OPS-9338"
        payload = task_payload(task_id)
        payload["metadata"] = {"test_case": task_id, "audit_required": True}
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task_graph(conn, payload, actor_kind="test", actor_id="central.runtime.tests")
                parent = task_db.fetch_task_snapshots(conn, task_id=task_id)[0]
                task_db.reconcile_task(
                    conn,
                    task_id=task_id,
                    expected_version=int(parent["version"]),
                    outcome="awaiting_audit",
                    summary="implementation complete",
                    notes="ready for audit",
                    tests="synthetic",
                    artifacts=[],
                    actor_kind="planner",
                    actor_id="central.runtime.tests",
                )
        finally:
            conn.close()

        audit_task_id = f"{task_id}-AUDIT"
        audit_payload = self._worker_result_payload(
            task_id=audit_task_id,
            run_id="run-audit",
            verdict="rework_required",
            summary="audit found unmet requirement",
        )
        dispatcher, state = self._prepare_worker_state(audit_task_id, "run-audit", audit_payload)

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher._finalize_worker(state)

        audit_snapshot = self.fetch_snapshot(audit_task_id)
        parent_snapshot = self.fetch_snapshot(task_id)
        self.assertEqual(audit_snapshot["runtime"]["runtime_status"], "done")
        self.assertEqual(audit_snapshot["planner_status"], "failed")
        self.assertEqual(parent_snapshot["planner_status"], "failed")
        self.assertEqual(parent_snapshot["metadata"].get("audit_verdict"), "failed")
        self.assertIn("planner.task_reconciled", self.fetch_events(audit_task_id))

    def test_auto_reconcile_failure_is_logged_and_surfaced(self) -> None:
        task_id = "CENTRAL-OPS-9332"
        self.create_task(task_payload(task_id))
        dispatcher = self.dispatcher()

        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id="central-worker:test:slot",
                queue_name="default",
                lease_seconds=30,
                task_id=task_id,
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()
        self.assertIsNotNone(claim)
        claim = claim or {}
        worker_id = str((claim.get("lease") or {}).get("lease_owner_id"))

        conn = task_db.connect(self.db_path)
        try:
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status="running",
                worker_id=worker_id,
                error_text=None,
                notes="manual test setup",
                artifacts=[],
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()

        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.update_task(
                    conn,
                    task_id=task_id,
                    payload={"planner_status": "blocked", "metadata": {"forced_drift": True}},
                    expected_version=1,
                    actor_kind="planner",
                    actor_id="central.runtime.tests",
                    allow_active_lease=True,
                )
        finally:
            conn.close()

        task_dir = dispatcher.paths.worker_results_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        result_path = task_dir / "run-1.json"
        result_path.write_text(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "schema_version": 1,
                    "task_id": task_id,
                    "run_id": "run-1",
                    "summary": "synthetic reconcile failure",
                    "completed_items": ["synthetic"],
                    "remaining_items": [],
                    "decisions": [],
                    "discoveries": [],
                    "blockers": [],
                    "validation": [{"name": "synthetic", "passed": True, "notes": "ok"}],
                    "files_changed": [],
                    "warnings": [],
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        prompt_path = dispatcher.paths.worker_prompts_dir / task_id / "run-1.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("synthetic prompt\n", encoding="utf-8")
        log_path = dispatcher.paths.worker_logs_dir / task_id / "run-1.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("synthetic log\n", encoding="utf-8")

        state = central_runtime.ActiveWorker(
            task=claim,
            worker_id=worker_id,
            run_id="run-1",
            pid=999999,
            proc=None,
            log_handle=None,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=0.0,
            timeout_seconds=30,
        )

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher._finalize_worker(state)

        snapshot = self.fetch_snapshot(task_id)
        self.assertIn(snapshot["planner_status"], {"blocked", "failed"})
        self.assertEqual(snapshot["runtime"]["runtime_status"], "done")
        mismatch = snapshot["status_mismatch"]
        if snapshot["planner_status"] != "failed":
            self.assertIsNotNone(mismatch)
            self.assertEqual(mismatch["severity"], "error")
        else:
            self.assertIsNone(mismatch)
        review_rows = task_db.format_review_rows([snapshot])
        if mismatch is not None:
            self.assertEqual(len(review_rows), 1)
            self.assertIn("runtime finished with done", review_rows[0]["status_warning"])
        else:
            self.assertEqual(len(review_rows), 0)
        self.assertIn("planner.task_auto_reconcile_failed", self.fetch_events(task_id))
        log_text = dispatcher.paths.log_path.read_text(encoding="utf-8")
        self.assertIn("worker_auto_reconcile_failed", log_text)

    def test_worker_crash_without_result_records_failure_metadata(self) -> None:
        task_id = "CENTRAL-OPS-9334"
        self.create_task(task_payload(task_id))
        dispatcher = self.dispatcher()

        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id="central-worker:test:slot",
                queue_name="default",
                lease_seconds=30,
                task_id=task_id,
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()
        self.assertIsNotNone(claim)
        claim = claim or {}
        worker_id = str((claim.get("lease") or {}).get("lease_owner_id"))

        conn = task_db.connect(self.db_path)
        try:
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status="running",
                worker_id=worker_id,
                error_text=None,
                notes="synthetic worker",
                artifacts=[],
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()

        result_path = dispatcher.paths.worker_results_dir / task_id / "run-1.json"
        prompt_path = dispatcher.paths.worker_prompts_dir / task_id / "run-1.md"
        log_path = dispatcher.paths.worker_logs_dir / task_id / "run-1.log"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("synthetic prompt\n", encoding="utf-8")
        log_path.write_text("synthetic log\n", encoding="utf-8")
        if result_path.exists():
            result_path.unlink()

        state = central_runtime.ActiveWorker(
            task=claim,
            worker_id=worker_id,
            run_id="run-1",
            pid=4242,
            proc=SimpleNamespace(returncode=17),
            log_handle=None,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=0.0,
            timeout_seconds=30,
        )

        dispatcher._finalize_worker(state)

        snapshot = self.fetch_snapshot(task_id)
        runtime = snapshot["runtime"]
        self.assertEqual(runtime["runtime_status"], "failed")
        self.assertEqual(runtime["last_runtime_error"], "worker_crashed (exit 17)")
        self.assertEqual(runtime["metadata"].get("notes"), "worker process exited with code 17")
        self.assertIsNone(snapshot["lease"])
        self.assertIsNone(snapshot["status_mismatch"])
        self.assertEqual(snapshot["planner_status"], "todo")

    def test_worker_result_with_foreign_task_id_is_rejected(self) -> None:
        task_id = "CENTRAL-OPS-9335"
        self.create_task(task_payload(task_id))
        payload = self._worker_result_payload(
            task_id=f"{task_id}-WRONG",
            run_id="run-mismatch",
            summary="synthetic foreign result",
        )
        dispatcher, state = self._prepare_worker_state(task_id, "run-mismatch", payload)

        runner = ValidatingAutonomyRunner(enforce_task_id=True)
        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=runner):
            dispatcher._finalize_worker(state)

        snapshot = self.fetch_snapshot(task_id)
        runtime = snapshot["runtime"]
        self.assertEqual(runtime["runtime_status"], "failed")
        self.assertIn("task_id mismatch", runtime["last_runtime_error"])
        self.assertEqual(snapshot["planner_status"], "todo")
        self.assertIsNone(snapshot["status_mismatch"])

    def test_worker_result_run_id_mismatch_is_normalized(self) -> None:
        task_id = "CENTRAL-OPS-9336"
        canonical_run = "run-canonical"
        self.create_task(task_payload(task_id))
        artifact_path = Path(self.tmpdir.name) / "worker-artifact.txt"
        artifact_path.write_text("synthetic artifact\n", encoding="utf-8")
        payload = self._worker_result_payload(
            task_id=task_id,
            run_id="run-from-worker",
            summary="synthetic success",
            artifacts=[{"path": str(artifact_path), "type": "file", "notes": "synthetic evidence"}],
        )
        dispatcher, state = self._prepare_worker_state(task_id, canonical_run, payload)

        runner = ValidatingAutonomyRunner(enforce_task_id=True)
        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=runner):
            dispatcher._finalize_worker(state)

        snapshot = self.fetch_snapshot(task_id)
        runtime = snapshot["runtime"]
        self.assertEqual(runtime["runtime_status"], "done")
        self.assertEqual(snapshot["planner_status"], "done")
        metadata_blob = json.dumps(snapshot.get("metadata") or {})
        self.assertNotIn(payload["run_id"], metadata_blob)

        conn = task_db.connect(self.db_path)
        try:
            artifact_rows = conn.execute(
                """
                SELECT artifact_kind, metadata_json
                FROM task_artifacts
                WHERE task_id = ? AND artifact_kind LIKE 'worker_%'
                ORDER BY artifact_id ASC
                """,
                (task_id,),
            ).fetchall()
        finally:
            conn.close()

        self.assertGreaterEqual(len(artifact_rows), 1)
        for row in artifact_rows:
            metadata = json.loads(str(row["metadata_json"]))
            self.assertEqual(metadata.get("run_id"), canonical_run)

    def test_codex_usage_limit_requeues_task_and_sets_backoff(self) -> None:
        task_id = "CENTRAL-OPS-9337"
        self.create_task(task_payload(task_id))
        dispatcher = central_runtime.CentralDispatcher(
            central_runtime.DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="codex",
            )
        )

        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id="central-worker:test:slot",
                queue_name="default",
                lease_seconds=30,
                task_id=task_id,
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()
        self.assertIsNotNone(claim)
        claim = claim or {}
        worker_id = str((claim.get("lease") or {}).get("lease_owner_id"))

        conn = task_db.connect(self.db_path)
        try:
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status="running",
                worker_id=worker_id,
                error_text=None,
                notes="synthetic codex worker",
                artifacts=[],
                actor_id="central.runtime.tests",
            )
        finally:
            conn.close()

        result_path = dispatcher.paths.worker_results_dir / task_id / "run-capacity.json"
        prompt_path = dispatcher.paths.worker_prompts_dir / task_id / "run-capacity.md"
        log_path = dispatcher.paths.worker_logs_dir / task_id / "run-capacity.log"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("", encoding="utf-8")
        prompt_path.write_text("synthetic prompt\n", encoding="utf-8")
        log_path.write_text(
            "{\"type\":\"error\",\"message\":\"You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage\"}\n",
            encoding="utf-8",
        )

        state = central_runtime.ActiveWorker(
            task=claim,
            worker_id=worker_id,
            run_id="run-capacity",
            pid=4242,
            proc=SimpleNamespace(returncode=0),
            log_handle=None,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=0.0,
            timeout_seconds=30,
            selected_worker_model="gpt-5.4",
            selected_worker_model_source="dispatcher_default",
            selected_worker_backend="codex",
        )

        with mock.patch.object(central_runtime, "load_autonomy_runner", return_value=FakeAutonomyRunner):
            dispatcher._finalize_worker(state)

        snapshot = self.fetch_snapshot(task_id)
        runtime = snapshot["runtime"]
        self.assertEqual(snapshot["planner_status"], "todo")
        self.assertEqual(runtime["runtime_status"], "queued")
        self.assertEqual(runtime["retry_count"], 0)
        self.assertIsNone(runtime["last_runtime_error"])
        self.assertIsNone(snapshot["lease"])
        self.assertTrue(dispatcher._capacity_backoff_active())
        events = self.fetch_events(task_id)
        self.assertIn("runtime.requeued", events)
        log_text = dispatcher.paths.log_path.read_text(encoding="utf-8")
        self.assertIn("worker_capacity_requeued", log_text)
        self.assertIn("dispatcher_capacity_backoff", log_text)


if __name__ == "__main__":
    unittest.main()
