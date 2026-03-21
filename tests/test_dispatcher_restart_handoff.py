#!/usr/bin/env python3
"""Restart handoff smoke test for the CENTRAL dispatcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_CONTROL = REPO_ROOT / "scripts" / "dispatcher_control.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


TASK_ID = "SMOKE-1"
WORKER_SLEEP_SECONDS = 14.0


def worker_task_payload() -> dict[str, object]:
    return {
        "task_id": TASK_ID,
        "initiative": "one-off",
        "title": "Dispatcher restart handoff smoke task",
        "summary": "Validate dispatcher restart-safe worker adoption",
        "objective_md": "Keep a long-running stub worker alive across dispatcher restart.",
        "context_md": "Synthetic runtime smoke task for CENTRAL-OPS-31.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- preserve supervision across dispatcher restart",
        "acceptance_md": "- worker keeps running and finalizes successfully after adoption",
        "testing_md": "- automated restart smoke only",
        "dispatch_md": "Dispatch locally through dispatcher_control.py with stub mode.",
        "closeout_md": "Inspect runtime state and events only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": {"smoke": True, "audit_required": False},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 60,
            "metadata": {
                "stub_sleep_seconds": WORKER_SLEEP_SECONDS,
                "stub_log_interval_seconds": 0.5,
            },
        },
        "dependencies": [],
    }


class DispatcherRestartHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_dispatcher_restart_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        self.state_dir = tmp_path / "runtime_state"
        self.env = os.environ.copy()
        self.env["CENTRAL_TASK_DB_PATH"] = str(self.db_path)
        self.env["CENTRAL_RUNTIME_STATE_DIR"] = str(self.state_dir)
        self.env["CENTRAL_WORKER_MODE"] = "stub"

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
                task_db.create_task(conn, worker_task_payload(), actor_kind="self_check", actor_id="central.runtime")
        finally:
            conn.close()

    def tearDown(self) -> None:
        try:
            self.run_dispatcher("stop", check=False, timeout=20)
        finally:
            worker_pid = self.worker_pid_from_snapshot()
            if worker_pid is not None:
                try:
                    os.kill(worker_pid, 15)
                except OSError:
                    pass
            self.tmpdir.cleanup()

    def run_dispatcher(self, *args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DISPATCHER_CONTROL), *args],
            cwd=str(REPO_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            self.fail(f"dispatcher {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def fetch_snapshot(self) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=TASK_ID)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def fetch_events(self) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (TASK_ID,),
            ).fetchall()
            return [str(row["event_type"]) for row in rows]
        finally:
            conn.close()

    def fetch_event_payloads(self, event_type: str) -> list[dict[str, object]]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM task_events WHERE task_id = ? AND event_type = ? ORDER BY event_id ASC",
                (TASK_ID, event_type),
            ).fetchall()
        finally:
            conn.close()
        payloads: list[dict[str, object]] = []
        for row in rows:
            raw = row["payload_json"]
            if not raw:
                payloads.append({})
                continue
            try:
                payloads.append(json.loads(str(raw)))
            except Exception:
                payloads.append({})
        return payloads

    def supervision_from_snapshot(self, snapshot: dict[str, object]) -> dict[str, object] | None:
        metadata = ((snapshot.get("lease") or {}).get("metadata") or {})
        supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
        if not isinstance(supervision, dict):
            return None
        return supervision

    def wait_for(self, predicate, *, timeout: float, interval: float = 0.2):
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except AssertionError as exc:
                last_error = exc
            time.sleep(interval)
        if last_error is not None:
            raise last_error
        self.fail("timed out waiting for condition")

    def worker_pid_from_snapshot(self) -> int | None:
        snapshot = self.fetch_snapshot()
        metadata = ((snapshot.get("lease") or {}).get("metadata") or {})
        supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
        if not isinstance(supervision, dict):
            return None
        try:
            return int(supervision.get("worker_pid"))
        except (TypeError, ValueError):
            return None

    def dispatcher_pid(self) -> int | None:
        lock_path = self.state_dir / "dispatcher.lock"
        if not lock_path.exists():
            return None
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid"))
        except Exception:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def worker_result_path(self, task_id: str, run_id: str) -> Path:
        return self.state_dir / ".worker-results" / task_id / f"{run_id}.json"

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def test_restart_adopts_running_worker(self) -> None:
        self.run_dispatcher("start", "--max-workers", "1")

        snapshot = self.wait_for(
            lambda: (
                current
                if isinstance((((current := self.fetch_snapshot()).get("lease") or {}).get("metadata") or {}).get("supervision"), dict)
                else None
            ),
            timeout=10.0,
        )
        initial_dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)
        initial_heartbeat = (snapshot.get("lease") or {}).get("last_heartbeat_at")
        worker_pid = self.worker_pid_from_snapshot()
        self.assertIsNotNone(worker_pid)
        os.kill(worker_pid, 0)

        restart_started = time.time()
        self.run_dispatcher("restart", "--max-workers", "1", timeout=20.0)
        restart_elapsed = time.time() - restart_started
        self.assertLess(restart_elapsed, WORKER_SLEEP_SECONDS - 4.0)
        os.kill(worker_pid, 0)

        adopted_dispatcher_pid = self.wait_for(
            lambda: pid if (pid := self.dispatcher_pid()) and pid != initial_dispatcher_pid else None,
            timeout=10.0,
        )
        self.assertNotEqual(initial_dispatcher_pid, adopted_dispatcher_pid)

        adopted_snapshot = self.wait_for(
            lambda: self.fetch_snapshot() if "runtime.worker_adopted" in self.fetch_events() else None,
            timeout=10.0,
        )
        adopted_heartbeat = (adopted_snapshot.get("lease") or {}).get("last_heartbeat_at")
        self.assertNotEqual(initial_heartbeat, adopted_heartbeat)

        finished_snapshot = self.wait_for(
            lambda: (
                current
                if str(((current := self.fetch_snapshot()).get("runtime") or {}).get("runtime_status") or "") == "done"
                else None
            ),
            timeout=WORKER_SLEEP_SECONDS + 15.0,
        )
        self.assertIsNone(finished_snapshot.get("lease"))
        self.assertIn("runtime.dispatcher_handoff_requested", self.fetch_events())
        self.assertIn("runtime.worker_adopted", self.fetch_events())

    def test_restart_adoption_preserves_run_and_terminal_state(self) -> None:
        """Dispatcher restart should adopt the live worker without double running or misclassifying state."""
        self.run_dispatcher("start", "--max-workers", "1")

        def snapshot_with_supervision() -> dict[str, object] | None:
            snapshot = self.fetch_snapshot()
            return snapshot if self.supervision_from_snapshot(snapshot) else None

        snapshot = self.wait_for(snapshot_with_supervision, timeout=10.0)
        supervision = self.supervision_from_snapshot(snapshot)
        self.assertIsNotNone(supervision)
        run_id = str(supervision.get("run_id") or "")
        self.assertTrue(run_id)
        worker_pid = self.worker_pid_from_snapshot()
        self.assertIsNotNone(worker_pid)
        os.kill(worker_pid, 0)

        initial_dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)
        self.run_dispatcher("restart", "--max-workers", "1", timeout=20.0)
        os.kill(worker_pid, 0)

        adopted_dispatcher_pid = self.wait_for(
            lambda: pid if (pid := self.dispatcher_pid()) and pid != initial_dispatcher_pid else None,
            timeout=10.0,
        )
        self.assertNotEqual(initial_dispatcher_pid, adopted_dispatcher_pid)

        def one_handoff_payload() -> list[dict[str, object]] | None:
            payloads = self.fetch_event_payloads("runtime.dispatcher_handoff_requested")
            return payloads if len(payloads) == 1 else None

        handoff_payloads = self.wait_for(one_handoff_payload, timeout=10.0)

        def one_adoption_payload() -> list[dict[str, object]] | None:
            payloads = self.fetch_event_payloads("runtime.worker_adopted")
            return payloads if len(payloads) == 1 else None

        adoption_payloads = self.wait_for(one_adoption_payload, timeout=10.0)

        self.assertEqual(str(handoff_payloads[0].get("run_id")), run_id)
        self.assertEqual(str(adoption_payloads[0].get("run_id")), run_id)
        self.assertEqual(int(adoption_payloads[0].get("worker_pid")), worker_pid)

        adopted_snapshot = self.fetch_snapshot()
        adopted_supervision = self.supervision_from_snapshot(adopted_snapshot)
        self.assertIsNotNone(adopted_supervision)
        self.assertEqual(str(adopted_supervision.get("run_id")), run_id)
        self.assertEqual(int(adopted_supervision.get("worker_pid")), worker_pid)

        handoff_state = (((adopted_snapshot.get("lease") or {}).get("metadata") or {}).get("handoff") or {})
        self.assertEqual(handoff_state.get("state"), "adopted")
        self.assertNotIn("runtime.worker_interrupted", self.fetch_events())

        def finished_snapshot() -> dict[str, object] | None:
            current = self.fetch_snapshot()
            runtime_status = str((current.get("runtime") or {}).get("runtime_status") or "")
            return current if runtime_status == "done" else None

        final_snapshot = self.wait_for(finished_snapshot, timeout=WORKER_SLEEP_SECONDS + 15.0)
        self.assertIsNone(final_snapshot.get("lease"))
        self.assertEqual(str((final_snapshot.get("runtime") or {}).get("runtime_status") or ""), "done")

        status_payloads = self.fetch_event_payloads("runtime.status_transition")
        failed_transitions = [payload for payload in status_payloads if str(payload.get("status") or "") == "failed"]
        self.assertFalse(failed_transitions, f"Unexpected failure transitions present: {failed_transitions}")
        done_transitions = [payload for payload in status_payloads if str(payload.get("status") or "") == "done"]
        self.assertTrue(done_transitions, "Missing done transition after worker adoption restart")

    def test_worker_result_recovered_after_dispatcher_downtime(self) -> None:
        """Worker finishing while dispatcher is stopped should be reconciled without re-running."""
        self.run_dispatcher("start", "--max-workers", "1")

        def snapshot_with_supervision() -> dict[str, object] | None:
            snapshot = self.fetch_snapshot()
            return snapshot if self.supervision_from_snapshot(snapshot) else None

        snapshot = self.wait_for(snapshot_with_supervision, timeout=10.0)
        supervision = self.supervision_from_snapshot(snapshot)
        self.assertIsNotNone(supervision)
        run_id = str(supervision.get("run_id") or "")
        self.assertTrue(run_id)
        result_path = self.worker_result_path(TASK_ID, run_id)
        self.assertFalse(result_path.exists())

        worker_pid = self.worker_pid_from_snapshot()
        self.assertIsNotNone(worker_pid)
        self.assertTrue(self._pid_alive(worker_pid))

        dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)
        self.assertIsNotNone(dispatcher_pid)

        # Stop dispatcher while the worker continues running to simulate downtime.
        self.run_dispatcher("stop", timeout=20.0)
        self.wait_for(lambda: self.dispatcher_pid() is None, timeout=10.0)

        # Worker should remain alive briefly, then finish and emit a result while dispatcher is down.
        self.assertTrue(self._pid_alive(worker_pid))
        self.wait_for(lambda: result_path.exists(), timeout=WORKER_SLEEP_SECONDS + 10.0)
        self.wait_for(lambda: not self._pid_alive(worker_pid), timeout=10.0)

        # Without a dispatcher, the task should still appear running with an active lease.
        lingering_snapshot = self.fetch_snapshot()
        runtime_status = str((lingering_snapshot.get("runtime") or {}).get("runtime_status") or "")
        self.assertEqual(runtime_status, "running", lingering_snapshot)
        self.assertIsNotNone(lingering_snapshot.get("lease"))

        # Bring the dispatcher back up — it should reconcile the completed result and close the lease.
        self.run_dispatcher("start", "--max-workers", "1")
        self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)

        def reconciled_snapshot() -> dict[str, object] | None:
            current = self.fetch_snapshot()
            runtime = current.get("runtime") or {}
            return current if str(runtime.get("runtime_status") or "") == "done" else None

        final_snapshot = self.wait_for(reconciled_snapshot, timeout=20.0)
        runtime = final_snapshot.get("runtime") or {}
        self.assertIsNone(final_snapshot.get("lease"))
        self.assertEqual(str(final_snapshot.get("planner_status") or ""), "done")
        self.assertEqual(str(runtime.get("runtime_status") or ""), "done")
        self.assertEqual(int(runtime.get("retry_count") or 0), 0)
        self.assertIsNone(final_snapshot.get("status_mismatch"))

        closeout = ((final_snapshot.get("metadata") or {}).get("closeout") or {})
        self.assertTrue(closeout, "Expected runtime closeout metadata")
        self.assertEqual(closeout.get("outcome"), "done")
        self.assertEqual(closeout.get("summary"), "stub worker completed")
        runtime_notes = (runtime.get("metadata") or {}).get("notes")
        self.assertEqual(runtime_notes, "stub worker completed")


class InterruptClassificationTest(unittest.TestCase):
    """Test that force-stop and dead-worker-on-adoption produce explicit failure classifications."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_interrupt_class_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        self.state_dir = tmp_path / "runtime_state"
        self.env = os.environ.copy()
        self.env["CENTRAL_TASK_DB_PATH"] = str(self.db_path)
        self.env["CENTRAL_RUNTIME_STATE_DIR"] = str(self.state_dir)
        self.env["CENTRAL_WORKER_MODE"] = "stub"

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
                task_db.create_task(conn, self._task_payload("SMOKE-2"), actor_kind="self_check", actor_id="central.runtime")
        finally:
            conn.close()

    def tearDown(self) -> None:
        try:
            self.run_dispatcher("stop", check=False, timeout=20)
        finally:
            worker_pid = self._worker_pid_from_db("SMOKE-2")
            if worker_pid is not None:
                try:
                    os.kill(worker_pid, 15)
                except OSError:
                    pass
            self.tmpdir.cleanup()

    def _task_payload(self, task_id: str) -> dict[str, object]:
        return {
            "task_id": task_id,
            "initiative": "one-off",
            "title": "Interrupt classification smoke task",
            "summary": "Validate interrupted_by_restart classification",
            "objective_md": "Test explicit failure classification on interruption.",
            "context_md": "Synthetic runtime smoke task.",
            "scope_md": "No repo mutation required.",
            "deliverables_md": "- explicit interruption classification",
            "acceptance_md": "- error_text is interrupted_by_restart not generic",
            "testing_md": "- automated smoke only",
            "dispatch_md": "Dispatch locally through dispatcher_control.py with stub mode.",
            "closeout_md": "Inspect runtime state and events only.",
            "reconciliation_md": "CENTRAL DB remains canonical.",
            "planner_status": "todo",
            "priority": 1,
            "task_type": "implementation",
            "planner_owner": "planner/coordinator",
            "worker_owner": None,
            "target_repo_id": "CENTRAL",
            "target_repo_root": str(REPO_ROOT),
            "approval_required": False,
            "metadata": {"smoke": True},
            "execution": {
                "task_kind": "read_only",
                "sandbox_mode": "workspace-write",
                "approval_policy": "never",
                "additional_writable_dirs": [],
                "timeout_seconds": 60,
                "metadata": {
                    "stub_sleep_seconds": 20.0,
                    "stub_log_interval_seconds": 0.5,
                },
            },
            "dependencies": [],
        }

    def run_dispatcher(self, *args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DISPATCHER_CONTROL), *args],
            cwd=str(REPO_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            self.fail(f"dispatcher {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def fetch_snapshot(self, task_id: str) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def fetch_event_types(self, task_id: str) -> list[str]:
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
            return [str(row["event_type"]) for row in rows]
        finally:
            conn.close()

    def wait_for(self, predicate, *, timeout: float, interval: float = 0.2):
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except AssertionError as exc:
                last_error = exc
            time.sleep(interval)
        if last_error is not None:
            raise last_error
        self.fail("timed out waiting for condition")

    def _worker_pid_from_db(self, task_id: str) -> int | None:
        try:
            snapshot = self.fetch_snapshot(task_id)
        except Exception:
            return None
        metadata = ((snapshot.get("lease") or {}).get("metadata") or {})
        supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
        if not isinstance(supervision, dict):
            return None
        try:
            return int(supervision.get("worker_pid"))
        except (TypeError, ValueError):
            return None

    def dispatcher_pid(self) -> int | None:
        lock_path = self.state_dir / "dispatcher.lock"
        if not lock_path.exists():
            return None
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid"))
        except Exception:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def _fetch_interrupted_transition_error(self, task_id: str) -> str | None:
        """Return error text from the first runtime.status_transition event with status=failed, or None."""
        conn = task_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM task_events WHERE task_id = ? AND event_type = 'runtime.status_transition' ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
        finally:
            conn.close()
        import json as _json
        for row in rows:
            try:
                payload = _json.loads(str(row["payload_json"] or "{}"))
            except Exception:
                continue
            if str(payload.get("status") or "") == "failed":
                return str(payload.get("error") or "")
        return None

    def _wait_for_dispatcher_dead(self, pid: int, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except OSError:
                return

    def test_dispatcher_stop_then_worker_crash_classifies_as_interrupted_by_restart(self) -> None:
        """Dispatcher stop with worker dying in the handoff gap → interrupted_by_restart, not generic failure."""
        self.run_dispatcher("start", "--max-workers", "1")

        # Wait for worker to be claimed and supervision recorded
        self.wait_for(
            lambda: (
                current
                if isinstance((((current := self.fetch_snapshot("SMOKE-2")).get("lease") or {}).get("metadata") or {}).get("supervision"), dict)
                else None
            ),
            timeout=10.0,
        )
        worker_pid = self._worker_pid_from_db("SMOKE-2")
        self.assertIsNotNone(worker_pid)
        dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=5.0)

        # Simulate a graceful dispatcher stop followed by worker death in the handoff gap.
        # This is the primary interrupt scenario: dispatcher stops cleanly (pending_adoption),
        # but the worker process dies before the new dispatcher can adopt it.
        os.kill(dispatcher_pid, 15)  # graceful SIGTERM → sets pending_adoption handoff
        self._wait_for_dispatcher_dead(dispatcher_pid)

        # Kill the worker to simulate crash in the handoff gap
        try:
            os.kill(worker_pid, 9)
        except OSError:
            pass

        # Start new dispatcher — adoption should classify as interrupted_by_restart
        self.run_dispatcher("start", "--max-workers", "1")
        self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)

        # Check events for the interrupted_by_restart failure classification.
        # We check events (not runtime_status) because the task may be immediately re-dispatched.
        self.wait_for(
            lambda: self._fetch_interrupted_transition_error("SMOKE-2") is not None,
            timeout=15.0,
        )
        error_text = self._fetch_interrupted_transition_error("SMOKE-2")
        self.assertEqual(
            error_text,
            "interrupted_by_restart",
            f"Expected interrupted_by_restart, got: {error_text!r}",
        )

    def test_dead_worker_on_adoption_with_pending_adoption_classified_as_interrupted(self) -> None:
        """A dead worker found during adoption with pending_adoption handoff state → interrupted_by_restart."""
        self.run_dispatcher("start", "--max-workers", "1")

        # Wait for worker supervision to be recorded
        self.wait_for(
            lambda: (
                current
                if isinstance((((current := self.fetch_snapshot("SMOKE-2")).get("lease") or {}).get("metadata") or {}).get("supervision"), dict)
                else None
            ),
            timeout=10.0,
        )
        worker_pid = self._worker_pid_from_db("SMOKE-2")
        self.assertIsNotNone(worker_pid)

        # Graceful stop — sets handoff state to pending_adoption
        initial_dispatcher_pid = self.wait_for(lambda: self.dispatcher_pid(), timeout=5.0)
        os.kill(initial_dispatcher_pid, 15)
        self._wait_for_dispatcher_dead(initial_dispatcher_pid)

        # Kill the worker process to simulate crash during handoff gap
        try:
            os.kill(worker_pid, 9)
        except OSError:
            pass

        # Start new dispatcher — should find dead worker with pending_adoption → interrupted_by_restart
        self.run_dispatcher("start", "--max-workers", "1")
        self.wait_for(lambda: self.dispatcher_pid(), timeout=10.0)

        # Check events (not runtime_status) — task may be re-dispatched before we can read it
        self.wait_for(
            lambda: self._fetch_interrupted_transition_error("SMOKE-2") is not None,
            timeout=15.0,
        )
        error_text = self._fetch_interrupted_transition_error("SMOKE-2")
        self.assertEqual(
            error_text,
            "interrupted_by_restart",
            f"Expected interrupted_by_restart for dead worker on adoption, got: {error_text!r}",
        )


if __name__ == "__main__":
    unittest.main()
