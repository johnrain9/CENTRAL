#!/usr/bin/env python3
"""Focused coverage for dispatcher live-tail readability."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime
import central_task_db as task_db


def task_payload(
    task_id: str,
    *,
    priority: int,
    planner_status: str = "todo",
    dependencies: list[str] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} dispatcher log readability",
        "summary": "Exercise dispatcher live-tail formatting.",
        "objective_md": f"Exercise dispatcher readability for {task_id}.",
        "context_md": "Temporary DB only.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- emit realistic dispatcher lines",
        "acceptance_md": "- queue and exception lines remain scanable",
        "testing_md": "- focused unittest only",
        "dispatch_md": "Synthetic runtime coverage only.",
        "closeout_md": "Inspect emitted log lines only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": planner_status,
        "priority": priority,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
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
        "dependencies": dependencies or [],
    }


class DispatcherLogReadabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="dispatcher_log_readability_")
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
            task_db.create_task_graph(
                conn,
                task_payload("READY-1", priority=1),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
            task_db.create_task_graph(
                conn,
                task_payload("RUN-1", priority=2),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
            task_db.create_task_graph(
                conn,
                task_payload("BLOCKER-1", priority=3, planner_status="in_progress"),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
            task_db.create_task_graph(
                conn,
                task_payload("PARKED-1", priority=4, dependencies=["BLOCKER-1"]),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
            task_db.create_task_graph(
                conn,
                task_payload("REVIEW-1", priority=5),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
            task_db.create_task_graph(
                conn,
                task_payload("FAIL-1", priority=6),
                actor_kind="test",
                actor_id="dispatcher.log.tests",
                skip_preflight=True,
            )
        finally:
            conn.close()

        self.dispatcher = central_runtime.CentralDispatcher(
            central_runtime.DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=2,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="stub",
            )
        )

        self._set_runtime_status("RUN-1", "running", worker_id="worker-run")
        self._set_runtime_status("REVIEW-1", "pending_review", worker_id="worker-review")
        self._set_runtime_status("FAIL-1", "failed", worker_id="worker-fail", error_text="synthetic failure")

        task_id = "RUN-1"
        task_snapshot = self._fetch_snapshot(task_id)
        prompt_path = self.dispatcher.paths.worker_prompts_dir / task_id / "run-1.md"
        result_path = self.dispatcher.paths.worker_results_dir / task_id / "run-1.json"
        log_path = self.dispatcher.paths.worker_logs_dir / task_id / "run-1.log"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("prompt\n", encoding="utf-8")
        log_path.write_text("worker log\n", encoding="utf-8")
        self.dispatcher._active[task_id] = central_runtime.ActiveWorker(
            task=task_snapshot,
            worker_id="worker-run",
            run_id="run-1",
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
            timeout_seconds=60,
        )
        self.dispatcher.logger.use_color = True

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _fetch_snapshot(self, task_id: str) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def _set_runtime_status(
        self,
        task_id: str,
        status: str,
        *,
        worker_id: str,
        error_text: str | None = None,
    ) -> None:
        conn = task_db.connect(self.db_path)
        try:
            claim = task_db.runtime_claim(
                conn,
                worker_id=worker_id,
                queue_name="default",
                lease_seconds=120,
                task_id=task_id,
                actor_id="dispatcher.log.tests",
            )
            self.assertIsNotNone(claim)
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status=status,
                worker_id=worker_id,
                error_text=error_text,
                notes=f"{task_id} -> {status}",
                artifacts=[],
                actor_id="dispatcher.log.tests",
            )
        finally:
            conn.close()

    def test_tail_separates_queue_snapshot_from_real_errors(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.dispatcher._emit_status_heartbeat(force=True)
            self.dispatcher.logger.emit(
                "ERR",
                "central.dispatcher",
                "worker_spawn_error task=ERR-1 error=boom",
            )

        raw_lines = self.dispatcher.paths.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(raw_lines), 2)
        heartbeat = raw_lines[0]
        self.assertIn("heartbeat state=running", heartbeat)
        self.assertIn("running_tasks=RUN-1", heartbeat)
        self.assertIn("eligible=", heartbeat)
        self.assertIn("next=READY-1", heartbeat)
        self.assertIn("parked=", heartbeat)
        self.assertIn("review=1", heartbeat)
        self.assertIn("failed=", heartbeat)

        tail = self.dispatcher.logger.tail(lines=5, colorize=True)
        self.assertIn("HEARTBEAT", tail)
        self.assertIn("tasks=", tail)
        self.assertIn("review=", tail)
        self.assertIn("failed=", tail)
        self.assertIn("ISSUE", tail)
        self.assertIn("worker_spawn_error task=ERR-1 error=boom", tail)


if __name__ == "__main__":
    unittest.main()
