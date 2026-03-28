#!/usr/bin/env python3
"""Dispatcher test coverage for builder/schema validation and model dispatch behavior."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
AUTONOMY_ROOT = REPO_ROOT.parent / "Dispatcher"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(AUTONOMY_ROOT) not in sys.path:
    sys.path.insert(0, str(AUTONOMY_ROOT))

import central_task_db as task_db  # type: ignore
import repo_health_check  # type: ignore
from central_runtime_v2.config import DispatcherConfig  # type: ignore
from central_runtime_v2 import backends  # type: ignore
from central_runtime_v2.dispatcher import CentralDispatcher  # type: ignore
from central_runtime_v2 import model_policy  # type: ignore
from central_runtime_v2 import dispatcher as dispatcher_module  # type: ignore
from autonomy import runner as autonomy_runner  # type: ignore


REQUIRED_WORKER_FIELDS = {
    "status",
    "schema_version",
    "task_id",
    "run_id",
    "summary",
    "verdict",
    "completed_items",
    "remaining_items",
    "decisions",
    "discoveries",
    "blockers",
    "validation",
    "requirements_assessment",
    "system_fit_assessment",
}

TEMP_DIR_PATTERNS = (
    "central-runtime-v2-dispatcher-test-*",
    "dispatcher-claude-test-*",
    "dispatcher-claude-flags-*",
    "dispatcher-stub-test-*",
    "dispatcher-stub-validate-*",
    "dispatcher-codex-cmd-*",
    "central-runtime-v2-spawn-claude-*",
    "central-runtime-v2-spawn-model-*",
    "repo-health-check-test-*",
)


def _wait_for_pid_exit(pid: int | None, *, timeout: float) -> bool:
    if pid is None or pid <= 0:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def _terminate_worker_process(
    proc: subprocess.Popen[str] | None,
    pid: int | None,
    *,
    term_timeout: float = 5.0,
    kill_timeout: float = 1.0,
) -> None:
    if proc is not None and proc.poll() is not None:
        return
    if pid is None or pid <= 0:
        return
    try:
        if proc is not None:
            proc.terminate()
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    except Exception:
        pass
    if proc is not None:
        try:
            proc.wait(timeout=term_timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
    elif _wait_for_pid_exit(pid, timeout=term_timeout):
        return

    try:
        if proc is not None:
            proc.kill()
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        return
    except Exception:
        pass
    if proc is not None:
        try:
            proc.wait(timeout=kill_timeout)
        except Exception:
            pass
    else:
        _wait_for_pid_exit(pid, timeout=kill_timeout)


def _remove_tree(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _cleanup_test_tmpdirs() -> None:
    tmp_root = Path(tempfile.gettempdir())
    for pattern in TEMP_DIR_PATTERNS:
        for path in tmp_root.glob(pattern):
            _remove_tree(path)


def _cleanup_test_tmpdirs_at_exit() -> None:
    time.sleep(0.5)
    _cleanup_test_tmpdirs()


def _cleanup_dispatcher_runtime(dispatcher: CentralDispatcher | None, tmpdir: tempfile.TemporaryDirectory[str] | None) -> None:
    if tmpdir is None:
        if dispatcher is not None:
            for task_id, state in list(dispatcher._active.items()):
                _terminate_worker_process(state.proc, state.pid)
                dispatcher._close_worker_state(state)
                dispatcher._active.pop(task_id, None)
        return
    try:
        if dispatcher is not None:
            for task_id, state in list(dispatcher._active.items()):
                _terminate_worker_process(state.proc, state.pid)
                dispatcher._close_worker_state(state)
                dispatcher._active.pop(task_id, None)
    finally:
        tmp_path = Path(tmpdir.name)
        tmpdir.cleanup()
        _remove_tree(tmp_path)


@contextmanager
def managed_tmpdir(prefix: str):
    tmp_path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield tmp_path
    finally:
        _remove_tree(tmp_path)


unittest.addModuleCleanup(_cleanup_test_tmpdirs)
atexit.register(_cleanup_test_tmpdirs_at_exit)


def task_payload(
    task_id: str,
    *,
    execution_metadata: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    task_metadata = {"audit_required": False}
    if metadata:
        task_metadata.update(metadata)
    task_execution_metadata = {"stub_sleep_seconds": 0.05, "stub_log_interval_seconds": 0.01}
    if execution_metadata:
        task_execution_metadata.update(execution_metadata)
    return {
        "task_id": task_id,
        "title": f"{task_id} dispatcher test",
        "summary": "Exercise dispatcher behavior in unit/integration tests.",
        "objective_md": f"Execute test {task_id}.",
        "context_md": "Synthetic test task.",
        "scope_md": "No persistent repo mutations.",
        "deliverables_md": "- validate runtime behavior",
        "acceptance_md": "- test suite assertions",
        "testing_md": "- automated",
        "dispatch_md": "local test dispatch",
        "closeout_md": "no additional steps",
        "reconciliation_md": "CENTRAL DB remains canonical",
        "planner_status": "todo",
        "priority": 1,
        "initiative": "one-off",
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "metadata": task_metadata,
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": task_execution_metadata,
        },
        "dependencies": [],
    }


def attach_preflight(
    conn: task_db.sqlite3.Connection,
    payload: dict[str, object],
    *,
    actor_id: str = "dispatcher.tests",
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
        "novelty_rationale": "No material overlap detected in dispatcher test setup.",
        "related_task_ids": [],
        "related_capability_ids": [],
    }
    return enriched


class BuildersTest(unittest.TestCase):
    def test_build_claude_command_output_validates_with_mock_prompt(self) -> None:
        task_id = "CENTRAL-OPS-59-CLAUDE"
        run_id = "run-cls-1"
        with managed_tmpdir("dispatcher-claude-test-") as tmp_path:
            result_path = tmp_path / "result.json"
            fake_claude = tmp_path / "claude"
            fake_claude.write_text(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "print(json.dumps({'type':'result','is_error':False,'result':'mock response from prompt'}))\n",
                encoding="utf-8",
            )
            fake_claude.chmod(0o755)
            command = backends.build_claude_command(
                {"id": task_id, "run_id": run_id},
                result_path,
                model="gpt-5.4",
            )

            proc = subprocess.run(
                command,
                input="prompt body",
                text=True,
                env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            autonomy_runner.validate_worker_payload(payload, task_id=task_id, run_id=run_id)
            self.assertEqual(payload["status"], "COMPLETED")

    def test_build_claude_command_uses_verbose_and_model(self) -> None:
        with managed_tmpdir("dispatcher-claude-flags-") as tmp_path:
            command = backends.build_claude_command(
                {"id": "TASK-59", "run_id": "run-flags"},
                tmp_path / "result.json",
                model="gpt-5.4-variant",
            )
            # build_claude_command emits a python -c wrapper; inspect embedded script string.
            script = command[2]
            self.assertIn("--verbose", script)
            self.assertIn("--model", script)
            self.assertIn("gpt-5.4-variant", script)

    def test_build_claude_command_appends_extra_args(self) -> None:
        with managed_tmpdir("dispatcher-claude-flags-") as tmp_path:
            command = backends.build_claude_command(
                {"id": "TASK-59", "run_id": "run-extra"},
                tmp_path / "result.json",
                model="gpt-5.4-variant",
                extra_args=["--resume", "sess-123", "--fork-session"],
            )

            script = command[2]
            self.assertIn("--resume", script)
            self.assertIn("sess-123", script)
            self.assertIn("--fork-session", script)

    def test_claude_backend_prepare_uses_session_fork_args(self) -> None:
        backend = backends.ClaudeBackend()
        snapshot = {"task_id": "TASK-59", "target_repo_id": "TEST"}
        worker_task = {
            "prompt_body": "prompt body",
            "worker_model": "claude-sonnet-4-6",
            "db_path": "/tmp/test.db",
        }
        fork_result = backends.session_manager.SessionForkResult(
            args=["--resume", "sess-123", "--fork-session"],
            session_id="sess-123",
            stale=False,
            stale_reason=None,
        )

        with (
            mock.patch.object(backends.session_manager, "get_fork_args", return_value=fork_result) as get_fork_args_mock,
            mock.patch.object(backend, "_log_session_fork") as log_session_fork_mock,
        ):
            prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, "run-1", Path("/tmp/result.json"))

        self.assertEqual(prompt_text, "prompt body")
        self.assertEqual(stdin_mode, subprocess.PIPE)
        get_fork_args_mock.assert_called_once_with("TEST", Path("/tmp/test.db"))
        log_session_fork_mock.assert_called_once_with("TASK-59", "TEST", Path("/tmp/test.db"), fork_result)
        self.assertIn("--resume", command[2])
        self.assertIn("sess-123", command[2])
        self.assertEqual(worker_task["run_id"], "run-1")

    def test_claude_backend_prepare_cold_starts_without_session_fork(self) -> None:
        backend = backends.ClaudeBackend()
        snapshot = {"task_id": "TASK-59", "target_repo_id": "TEST"}
        worker_task = {
            "prompt_body": "prompt body",
            "worker_model": "claude-sonnet-4-6",
            "db_path": "/tmp/test.db",
        }

        with (
            mock.patch.object(backends.session_manager, "get_fork_args", return_value=None) as get_fork_args_mock,
            mock.patch.object(backend, "_log_session_fork") as log_session_fork_mock,
        ):
            prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, "run-1", Path("/tmp/result.json"))

        self.assertEqual(prompt_text, "prompt body")
        self.assertEqual(stdin_mode, subprocess.PIPE)
        get_fork_args_mock.assert_called_once_with("TEST", Path("/tmp/test.db"))
        log_session_fork_mock.assert_not_called()
        self.assertNotIn("--resume", command[2])
        self.assertNotIn("--fork-session", command[2])
        self.assertEqual(worker_task["run_id"], "run-1")

    def test_claude_backend_log_session_fork_emits_stale_events(self) -> None:
        backend = backends.ClaudeBackend()
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = {"fork_count": 12}
        result = backends.session_manager.SessionForkResult(
            args=["--resume", "sess-123", "--fork-session"],
            session_id="sess-123",
            stale=True,
            stale_reason="fork_count_exceeded(50)",
        )

        with (
            mock.patch.object(backends.task_db, "connect", return_value=conn) as connect_mock,
            mock.patch.object(backends.task_db, "insert_event") as insert_event_mock,
        ):
            backend._log_session_fork("TASK-59", "TEST", Path("/tmp/test.db"), result)

        connect_mock.assert_called_once_with(Path("/tmp/test.db"))
        conn.execute.assert_called_once_with(
            "SELECT fork_count FROM session_registry WHERE session_id = ?",
            ("sess-123",),
        )
        self.assertEqual(insert_event_mock.call_count, 2)
        first_call = insert_event_mock.call_args_list[0]
        self.assertEqual(first_call.kwargs["event_type"], "session.forked")
        self.assertEqual(first_call.kwargs["payload"]["fork_count"], 12)
        self.assertTrue(first_call.kwargs["payload"]["stale"])
        second_call = insert_event_mock.call_args_list[1]
        self.assertEqual(second_call.kwargs["event_type"], "session.stale_detected")
        self.assertEqual(second_call.kwargs["payload"]["reason"], "fork_count_exceeded(50)")
        conn.commit.assert_called_once_with()

    def test_build_stub_command_has_required_fields(self) -> None:
        snapshot = task_payload("TASK-59-STUB-FIELDS")
        run_id = "run-stub-fields"
        with managed_tmpdir("dispatcher-stub-test-") as tmp_path:
            result_path = tmp_path / "result.json"
            proc = subprocess.run(
                backends.build_stub_command(snapshot, run_id, result_path),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            for key in REQUIRED_WORKER_FIELDS:
                self.assertIn(key, payload)

    def test_build_stub_command_payload_validates(self) -> None:
        snapshot = task_payload("TASK-59-STUB-VALIDATE")
        run_id = "run-stub-validate"
        with managed_tmpdir("dispatcher-stub-validate-") as tmp_path:
            result_path = tmp_path / "result.json"
            subprocess.run(
                backends.build_stub_command(snapshot, run_id, result_path),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            autonomy_runner.validate_worker_payload(payload, task_id=snapshot["task_id"], run_id=run_id)
            self.assertEqual(payload["status"], "COMPLETED")

    def test_build_codex_command_includes_model_and_effort(self) -> None:
        with managed_tmpdir("dispatcher-codex-cmd-") as tmp_path:
            task = {
                "repo_root": str(REPO_ROOT),
                "approval_policy": "never",
                "codex_profile": "default",
                "codex_model": "gpt-5.4",
                "codex_effort": "high",
                "sandbox_mode": "workspace-write",
                "additional_writable_dirs_json": '["/tmp"]',
            }
            command = autonomy_runner.build_codex_command(task, tmp_path / "result.json", tmp_path / "schema.json")
            self.assertIn("--model", command)
            self.assertIn("-c", command)
            model_index = command.index("--model")
            self.assertEqual(command[model_index + 1], "gpt-5.4")
            effort_index = command.index("-c")
            self.assertEqual(command[effort_index + 1], 'model_reasoning_effort="high"')


class ModelRoutingTest(unittest.TestCase):
    def test_build_worker_task_codex_uses_config_default_model(self) -> None:
        snapshot = {
            "task_id": "CENTRAL-OPS-59-CODEX-DEFAULT",
            "title": "Codex default selection",
            "target_repo_root": str(REPO_ROOT),
            "objective_md": "Codex routing default check.",
            "scope_md": "scope",
            "testing_md": "tests",
            "deliverables_md": "deliverables",
            "acceptance_md": "acceptance",
            "context_md": "context",
            "dispatch_md": "dispatch",
            "reconciliation_md": "reconcile",
            "closeout_md": "closeout",
            "task_type": "implementation",
            "execution": {"metadata": {}},
            "metadata": {},
        }
        worker_task = model_policy.build_worker_task(
            snapshot,
            dispatcher_default_codex_model="codex-default-model",
            worker_mode="codex",
        )
        self.assertEqual(worker_task["worker_model"], "codex-default-model")
        self.assertEqual(worker_task.get("worker_model_source"), "dispatcher_default")
        self.assertEqual(worker_task.get("codex_model"), "codex-default-model")
        self.assertEqual(worker_task.get("codex_model_source"), "dispatcher_default")

    def test_build_worker_task_claude_uses_default_worker_model(self) -> None:
        snapshot = {
            "task_id": "CENTRAL-OPS-59-CLAUDE-DEFAULT",
            "title": "Claude routing default check.",
            "target_repo_root": str(REPO_ROOT),
            "objective_md": "Claude routing default check.",
            "scope_md": "scope",
            "testing_md": "tests",
            "deliverables_md": "deliverables",
            "acceptance_md": "acceptance",
            "context_md": "context",
            "dispatch_md": "dispatch",
            "reconciliation_md": "reconcile",
            "closeout_md": "closeout",
            "task_type": "implementation",
            "execution": {"metadata": {}},
            "metadata": {},
        }
        worker_task = model_policy.build_worker_task(
            snapshot,
            dispatcher_default_codex_model="ignored-codex-default",
            worker_mode="claude",
            dispatcher_default_worker_model="claude-default-model",
        )
        self.assertEqual(worker_task["worker_model"], "claude-default-model")
        self.assertEqual(worker_task["worker_model_source"], "dispatcher_default")

    def test_build_worker_task_codex_override_takes_precedence(self) -> None:
        snapshot = {
            "task_id": "CENTRAL-OPS-59-OVERRIDE",
            "title": "Codex override check",
            "target_repo_root": str(REPO_ROOT),
            "objective_md": "Override check.",
            "scope_md": "scope",
            "testing_md": "tests",
            "deliverables_md": "deliveries",
            "acceptance_md": "acceptance",
            "context_md": "context",
            "dispatch_md": "dispatch",
            "reconciliation_md": "reconcile",
            "closeout_md": "closeout",
            "task_type": "implementation",
            "execution": {"metadata": {"codex_model": "task-override-model"}},
            "metadata": {"tags": ["routine"]},
        }
        worker_task = model_policy.build_worker_task(
            snapshot,
            dispatcher_default_codex_model="codex-default-model",
            worker_mode="codex",
        )
        self.assertEqual(worker_task["worker_model"], "task-override-model")
        self.assertEqual(worker_task["worker_model_source"], "task_override")


class FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _text: str) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakePopen:
    instances: list["FakePopen"] = []
    next_pid = 9100

    def __init__(self, command: list[str], **kwargs: object) -> None:
        FakePopen.instances.append(self)
        self.command = list(command)
        self.kwargs = kwargs
        self.pid = FakePopen.next_pid
        FakePopen.next_pid += 1
        self.stdin = FakePipe() if kwargs.get("stdin") == subprocess.PIPE else None

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None


class DispatcherIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_path = Path(tempfile.mkdtemp(prefix="central-runtime-v2-dispatcher-unit-"))
        self.db_path = self._tmp_path / "central_tasks.db"
        self.state_dir = self._tmp_path / "runtime_state"
        self.dispatcher: CentralDispatcher | None = None
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
        self.dispatcher = CentralDispatcher(
            DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="stub",
                default_worker_model="gpt-5.4",
            )
        )

    def tearDown(self) -> None:
        _cleanup_dispatcher_runtime(self.dispatcher, None)
        _remove_tree(self._tmp_path)
        self.dispatcher = None

    def create_task(
        self,
        task_id: str,
        *,
        execution_metadata: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        payload = task_payload(task_id, execution_metadata=execution_metadata, metadata=metadata)
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task_graph(
                    conn,
                    attach_preflight(conn, payload),
                    actor_kind="test",
                    actor_id="dispatcher.tests",
                )
        finally:
            conn.close()

    def create_task_direct(
        self,
        task_id: str,
        *,
        execution_metadata: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        payload = task_payload(task_id, execution_metadata=execution_metadata, metadata=metadata)
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(
                    conn,
                    payload,
                    actor_kind="test",
                    actor_id="dispatcher.tests",
                )
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

    def _set_runtime_done(self, task_id: str, notes: str = "test runtime done") -> None:
        conn = task_db.connect(self.db_path)
        try:
            now_iso = task_db.now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO task_runtime_state
                    (task_id, runtime_status, last_transition_at, retry_count, runtime_metadata_json)
                VALUES (?, 'queued', ?, 0, '{}')
                """,
                (task_id, now_iso),
            )
            conn.commit()
            task_db.runtime_transition(
                conn,
                task_id=task_id,
                status="done",
                worker_id=None,
                error_text=None,
                notes=notes,
                artifacts=[],
                actor_id="dispatcher.tests",
            )
        finally:
            conn.close()

    def _make_worker_state(self, task_id: str) -> dispatcher_module.ActiveWorker:
        snapshot = self.fetch_snapshot(task_id)
        run_id = f"run-{task_id.lower()}"
        worker_path = self.state_dir / "workers" / task_id
        worker_path.mkdir(parents=True, exist_ok=True)
        return dispatcher_module.ActiveWorker(
            task=snapshot,
            worker_id=f"central-worker:test:{task_id}",
            run_id=run_id,
            pid=1234,
            proc=None,
            log_handle=None,
            prompt_path=worker_path / f"{run_id}.md",
            result_path=worker_path / f"{run_id}.json",
            log_path=worker_path / f"{run_id}.log",
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=0.0,
            timeout_seconds=30,
        )

    def _setup_parent_and_audit(self, task_id: str) -> tuple[str, str]:
        self.create_task(task_id, metadata={"audit_required": True})
        conn = task_db.connect(self.db_path)
        try:
            parent_before = task_db.fetch_task_snapshots(conn, task_id=task_id)[0]
            audit_task_id = str((parent_before.get("metadata") or {}).get("child_audit_task_id") or f"{task_id}-AUDIT")
            task_db.reconcile_task(
                conn,
                task_id=task_id,
                expected_version=int(parent_before["version"]),
                outcome="awaiting_audit",
                summary="implementation complete",
                notes="ready for audit",
                tests="synthetic",
                artifacts=[],
                actor_kind="planner",
                actor_id="dispatcher.tests",
            )
            conn.commit()
        finally:
            conn.close()
        return task_id, audit_task_id

    def test_stub_cycle_moves_task_to_done(self) -> None:
        task_id = "CENTRAL-OPS-5901"
        self.create_task(task_id, execution_metadata={"stub_sleep_seconds": 0.05})
        ret = self.dispatcher.run_once(emit_result=False)
        self.assertEqual(ret, 0)
        snapshot = self.fetch_snapshot(task_id)
        self.assertEqual((snapshot.get("runtime") or {}).get("runtime_status"), "done")

    def test_stub_cycle_triggers_repo_health_snapshot_after_done(self) -> None:
        task_id = "CENTRAL-OPS-5903"
        self.create_task(task_id, execution_metadata={"stub_sleep_seconds": 0.05})
        with mock.patch.object(self.dispatcher, "_run_health_snapshot_in_background") as trigger_mock:
            ret = self.dispatcher.run_once(emit_result=False)
        self.assertEqual(ret, 0)
        trigger_mock.assert_called_once()

    def test_stub_result_payload_has_required_schema_fields(self) -> None:
        task_id = "CENTRAL-OPS-5902"
        self.create_task(task_id, execution_metadata={"stub_sleep_seconds": 0.05})
        self.dispatcher.run_once(emit_result=False)

        result_dir = self.dispatcher.paths.worker_results_dir / task_id
        result_files = list(result_dir.glob("*.json"))
        self.assertEqual(len(result_files), 1)
        payload = json.loads(result_files[0].read_text(encoding="utf-8"))
        self.assertGreater(len(payload), 0)
        for key in REQUIRED_WORKER_FIELDS:
            self.assertIn(key, payload, f"missing required key: {key}")
        autonomy_runner.validate_worker_payload(payload, task_id=task_id, run_id=payload["run_id"])

    def test_local_dispatch_skips_remote_only_task(self) -> None:
        self.create_task_direct("CENTRAL-OPS-7991")
        self.create_task_direct("CENTRAL-OPS-7992", metadata={"remote": True})
        snapshot = self.dispatcher._claim_next()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["task_id"], "CENTRAL-OPS-7991")
        self.assertIsNone(self.dispatcher._claim_next())

    def test_remote_only_claim_prefers_remote_tasks(self) -> None:
        self.create_task_direct("CENTRAL-OPS-7993", metadata={"remote": True})
        self.create_task_direct("CENTRAL-OPS-7994")
        conn = task_db.connect(self.db_path)
        try:
            snapshot = task_db.runtime_claim(
                conn,
                worker_id="remote:test-1",
                queue_name="remote",
                lease_seconds=15,
                task_id=None,
                actor_id="unit.tests",
                remote_only=True,
                raise_on_empty=False,
            )
        finally:
            conn.close()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["task_id"], "CENTRAL-OPS-7993")

    def test_run_daemon_remote_workers_requires_token(self) -> None:
        original_token = os.environ.get("CENTRAL_COORDINATION_TOKEN")
        if "CENTRAL_COORDINATION_TOKEN" in os.environ:
            del os.environ["CENTRAL_COORDINATION_TOKEN"]

        remote_dispatcher = CentralDispatcher(
            DispatcherConfig(
                db_path=self.db_path,
                state_dir=self.state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode="stub",
                default_worker_model="gpt-5.4",
                remote_workers_enabled=True,
                max_remote_workers=1,
            )
        )
        try:
            with mock.patch.object(dispatcher_module, "acquire_lock"), \
                mock.patch.object(dispatcher_module, "release_lock"), \
                mock.patch.object(dispatcher_module.CentralDispatcher, "_setup_signals"):
                ret = remote_dispatcher.run_daemon()
        finally:
            if original_token is not None:
                os.environ["CENTRAL_COORDINATION_TOKEN"] = original_token
        self.assertEqual(ret, 1)

    def test_reconcile_done_audit_pass_routes_and_forwards_raw_payload(self) -> None:
        parent_task_id, audit_task_id = self._setup_parent_and_audit("CENTRAL-OPS-5905")
        self._set_runtime_done(audit_task_id, notes="audit completed")
        state = self._make_worker_state(audit_task_id)
        raw_payload = {"status": "COMPLETED", "verdict": "accepted", "task_id": audit_task_id, "run_id": state.run_id}

        with mock.patch.object(dispatcher_module.task_db, "reconcile_audit_pass", wraps=task_db.reconcile_audit_pass) as pass_mock:
            self.dispatcher._reconcile_done(
                state,
                result=SimpleNamespace(verdict="accepted"),
                notes="audit passed",
                tests="n/a",
                result_artifacts=[],
                raw_result_payload=raw_payload,
            )

        pass_mock.assert_called_once()
        self.assertEqual(pass_mock.call_args.kwargs["worker_result"], raw_payload)
        audit_snapshot = self.fetch_snapshot(audit_task_id)
        parent_snapshot = self.fetch_snapshot(parent_task_id)
        self.assertEqual(audit_snapshot["planner_status"], "done")
        self.assertEqual(parent_snapshot["planner_status"], "done")
        self.assertEqual((parent_snapshot.get("metadata") or {}).get("audit_verdict"), "accepted")

    def test_reconcile_done_audit_pass_accepts_pass_and_done_aliases(self) -> None:
        parent_task_id, audit_task_id = self._setup_parent_and_audit("CENTRAL-OPS-5910")
        self._set_runtime_done(audit_task_id, notes="audit completed")
        state = self._make_worker_state(audit_task_id)

        with mock.patch.object(dispatcher_module.task_db, "reconcile_audit_pass", wraps=task_db.reconcile_audit_pass) as pass_mock:
            for verdict in ("pass", "done"):
                with self.subTest(verdict=verdict):
                    self.dispatcher._reconcile_done(
                        state,
                        result=SimpleNamespace(verdict=verdict),
                        notes=f"audit {verdict}",
                        tests=None,
                        result_artifacts=[],
                        raw_result_payload={"status": "COMPLETED", "verdict": verdict},
                    )

        self.assertEqual(pass_mock.call_count, 2)
        audit_snapshot = self.fetch_snapshot(audit_task_id)
        parent_snapshot = self.fetch_snapshot(parent_task_id)
        self.assertEqual(audit_snapshot["planner_status"], "done")
        self.assertEqual(parent_snapshot["planner_status"], "done")

    def test_reconcile_done_audit_rework_requeues_parent_and_audit(self) -> None:
        parent_task_id, audit_task_id = self._setup_parent_and_audit("CENTRAL-OPS-5906")
        self._set_runtime_done(audit_task_id, notes="audit completed")
        state = self._make_worker_state(audit_task_id)

        self.dispatcher._reconcile_done(
            state,
            result=SimpleNamespace(verdict="rework_required"),
            notes="critical failure: add missing raw payload handoff",
            tests="n/a",
            result_artifacts=[],
            raw_result_payload=None,
        )

        audit_snapshot = self.fetch_snapshot(audit_task_id)
        parent_snapshot = self.fetch_snapshot(parent_task_id)
        self.assertEqual(audit_snapshot["planner_status"], "todo")
        self.assertEqual((audit_snapshot.get("runtime") or {}).get("runtime_status"), "queued")
        self.assertEqual(parent_snapshot["planner_status"], "todo")
        self.assertEqual((parent_snapshot.get("runtime") or {}).get("runtime_status"), "queued")
        parent_meta = parent_snapshot.get("metadata") or {}
        self.assertEqual(parent_meta.get("audit_verdict"), "rework_required")
        self.assertEqual(parent_meta.get("rework_count"), 1)

    def test_reconcile_done_generic_auto_reconcile_path(self) -> None:
        task_id = "CENTRAL-OPS-5907"
        self.create_task(task_id)
        self._set_runtime_done(task_id, notes="worker completed")
        state = self._make_worker_state(task_id)

        with mock.patch.object(dispatcher_module.task_db, "auto_reconcile_runtime_success", wraps=task_db.auto_reconcile_runtime_success) as auto_mock:
            self.dispatcher._reconcile_done(
                state,
                result=SimpleNamespace(verdict="accepted"),
                notes="runtime completed successfully",
                tests="synthetic=pass",
                result_artifacts=["/tmp/runtime-artifact.txt"],
                raw_result_payload=None,
            )

        auto_mock.assert_called_once()
        snapshot = self.fetch_snapshot(task_id)
        closeout = (snapshot.get("metadata") or {}).get("closeout") or {}
        self.assertEqual(snapshot["planner_status"], "done")
        self.assertEqual(closeout.get("source"), "runtime_auto_reconcile")


class DispatcherConfigTest(unittest.TestCase):
    def _build_dispatcher(self, db_path: Path, state_dir: Path, *, worker_mode: str, default_model: str) -> CentralDispatcher:
        return CentralDispatcher(
            DispatcherConfig(
                db_path=db_path,
                state_dir=state_dir,
                max_workers=1,
                poll_interval=0.05,
                heartbeat_seconds=0.1,
                status_heartbeat_seconds=0.1,
                stale_recovery_seconds=0.1,
                worker_mode=worker_mode,
                default_worker_model=default_model,
            )
        )

    def test_config_default_worker_model_applies_to_claude_spawn(self) -> None:
        with managed_tmpdir("central-runtime-v2-spawn-claude-") as tmp_path:
            db_path = tmp_path / "central_tasks.db"
            state_dir = tmp_path / "runtime_state"
            conn = task_db.connect(db_path)
            try:
                task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
                with conn:
                    task_db.ensure_repo(
                        conn,
                        repo_id="CENTRAL",
                        repo_root=str(REPO_ROOT),
                        display_name="CENTRAL",
                    )
                task_payload_obj = task_payload("CENTRAL-OPS-5904")
                task_db.create_task_graph(
                    conn,
                    attach_preflight(conn, task_payload_obj),
                    actor_kind="test",
                    actor_id="dispatcher.tests",
                )
            finally:
                conn.close()

            dispatcher = self._build_dispatcher(
                db_path=db_path,
                state_dir=state_dir,
                worker_mode="claude",
                default_model="claude-config-model",
            )
            snapshot = dispatcher._claim_next()
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            FakePopen.instances.clear()
            with mock.patch.object(dispatcher_module.subprocess, "Popen", FakePopen), mock.patch.object(
                dispatcher_module, "process_start_token", return_value="token"
            ):
                dispatcher._spawn_worker(snapshot)

            self.assertEqual(len(FakePopen.instances), 1)
            spawned = FakePopen.instances[0]
            script = str(spawned.command[2])
            self.assertIn("--model", script)
            self.assertIn("claude-config-model", script)

            state = dispatcher._active.get("CENTRAL-OPS-5904")
            if state is not None:
                dispatcher._close_worker_state(state)
                dispatcher._active.pop("CENTRAL-OPS-5904", None)

    def test_config_default_worker_model_used_in_spawn_worker(self) -> None:
        with managed_tmpdir("central-runtime-v2-spawn-model-") as tmp_path:
            db_path = tmp_path / "central_tasks.db"
            state_dir = tmp_path / "runtime_state"
            conn = task_db.connect(db_path)
            try:
                task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
                with conn:
                    task_db.ensure_repo(
                        conn,
                        repo_id="CENTRAL",
                        repo_root=str(REPO_ROOT),
                        display_name="CENTRAL",
                    )
                payload = task_payload("CENTRAL-OPS-5900")
                task_db.create_task_graph(
                    conn,
                    attach_preflight(conn, payload),
                    actor_kind="test",
                    actor_id="dispatcher.tests",
                )
            finally:
                conn.close()

            dispatcher = CentralDispatcher(
                DispatcherConfig(
                    db_path=db_path,
                    state_dir=state_dir,
                    max_workers=1,
                    poll_interval=0.05,
                    heartbeat_seconds=0.1,
                    status_heartbeat_seconds=0.1,
                    stale_recovery_seconds=0.1,
                    worker_mode="codex",
                    default_worker_model="spawn-model-x",
                )
            )
            FakePopen.instances.clear()
            snapshot = dispatcher._claim_next()
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            with mock.patch.object(dispatcher_module.subprocess, "Popen", FakePopen), mock.patch.object(
                dispatcher_module, "process_start_token", return_value="token"
            ):
                dispatcher._spawn_worker(snapshot)

            self.assertEqual(len(FakePopen.instances), 1)
            spawned = FakePopen.instances[0]
            self.assertIn("--model", spawned.command)
            model_index = spawned.command.index("--model")
            self.assertEqual(spawned.command[model_index + 1], "spawn-model-x")

            snapshot_after = self.fetch_snapshot(db_path=db_path, task_id="CENTRAL-OPS-5900")
            runtime_notes = str(((snapshot_after.get("runtime") or {}).get("metadata") or {}).get("notes") or "")
            self.assertIn("model=spawn-model-x", runtime_notes)
            self.assertIn("model_source=dispatcher_default", runtime_notes)
            state = dispatcher._active.get("CENTRAL-OPS-5900")
            if state is not None:
                dispatcher._close_worker_state(state)
                dispatcher._active.pop("CENTRAL-OPS-5900", None)

    def fetch_snapshot(self, db_path: Path, task_id: str) -> dict[str, object]:
        conn = task_db.connect(db_path)
        try:
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            self.assertEqual(len(snapshots), 1)
            return snapshots[0]
        finally:
            conn.close()

    def test_create_task_graph_requires_preflight_but_helper_satisfies_it(self) -> None:
        with managed_tmpdir("central-runtime-v2-preflight-helper-") as tmp_path:
            db_path = tmp_path / "central_tasks.db"
            conn = task_db.connect(db_path)
            try:
                task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
                with conn:
                    task_db.ensure_repo(
                        conn,
                        repo_id="CENTRAL",
                        repo_root=str(REPO_ROOT),
                        display_name="CENTRAL",
                    )
                payload = task_payload("CENTRAL-OPS-5905")
                with self.assertRaises(SystemExit):
                    task_db.create_task_graph(conn, payload, actor_kind="test", actor_id="dispatcher.tests")
                task_db.create_task_graph(
                    conn,
                    attach_preflight(conn, payload),
                    actor_kind="test",
                    actor_id="dispatcher.tests",
                )
            finally:
                conn.close()

            snapshot = self.fetch_snapshot(db_path=db_path, task_id="CENTRAL-OPS-5905")
            self.assertEqual(snapshot["task_id"], "CENTRAL-OPS-5905")


class RepoHealthCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="repo-health-check-test-"))
        self.db_path = self.tmp_path / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
        finally:
            conn.close()

    def tearDown(self) -> None:
        _remove_tree(self.tmp_path)

    def test_repo_health_check_writes_structured_latest_snapshot(self) -> None:
        repo_root = self.tmp_path / "sample_python_repo"
        tests_dir = repo_root / "tests"
        tests_dir.mkdir(parents=True)
        (repo_root / "pyproject.toml").write_text("[project]\nname = 'sample-python-repo'\nversion = '0.0.1'\n", encoding="utf-8")
        (tests_dir / "test_sample.py").write_text(
            "import unittest\n\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id="sample-python-repo",
                    repo_root=str(repo_root),
                    display_name="Sample Python Repo",
                )
        finally:
            conn.close()

        report, wrote = repo_health_check.run(repo_root, ttl_seconds=3600, db_path=str(self.db_path))
        self.assertTrue(wrote)
        self.assertEqual(report["repo"]["repo_id"], "sample-python-repo")
        self.assertEqual(report["metadata"]["test_run"]["runner"], "python")
        self.assertEqual(report["metadata"]["test_run"]["counts"]["passed"], 1)

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "central_task_db.py"),
                "health-snapshot-latest",
                "--json",
                "--db-path",
                str(self.db_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        rows = json.loads(proc.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repo_id"], "sample-python-repo")
        self.assertEqual(rows[0]["runner"], "python")
        self.assertEqual(rows[0]["test_summary"]["counts"]["passed"], 1)
        self.assertIn("report", rows[0])

    def test_resolve_min_coverage_priority(self) -> None:
        repo_root = self.tmp_path / "sample_python_repo"
        repo_root.mkdir(parents=True)
        (repo_root / "pyproject.toml").write_text(
            "[project]\nname='sample-python-repo'\nversion='0.0.1'\n\n[tool.repo_health_check]\nmin_coverage = 71.5\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"REPO_HEALTH_MIN_COVERAGE": "63.2"}, clear=False):
            self.assertEqual(repo_health_check.resolve_min_coverage(repo_root, None), 63.2)
        self.assertEqual(repo_health_check.resolve_min_coverage(repo_root, 88.0), 88.0)
        with mock.patch.dict(os.environ, {"REPO_HEALTH_MIN_COVERAGE": ""}, clear=False):
            self.assertEqual(repo_health_check.resolve_min_coverage(repo_root, None), 71.5)

    def test_detect_runner_includes_cov_fail_under_when_available(self) -> None:
        repo_root = self.tmp_path / "sample_python_repo"
        repo_root.mkdir(parents=True)
        (repo_root / "pyproject.toml").write_text("[project]\nname='sample-python-repo'\nversion='0.0.1'\n", encoding="utf-8")
        with mock.patch.object(repo_health_check, "_python_candidates", return_value=["/tmp/python-under-test"]):
            with mock.patch.object(repo_health_check, "_module_available", return_value=True):
                runner, command = repo_health_check.detect_runner(repo_root, min_coverage=75.0)
        self.assertEqual(runner, "python")
        self.assertEqual(command[:3], ["/tmp/python-under-test", "-m", "pytest"])
        self.assertIn("--cov=.", command)
        self.assertIn("--cov-report=xml:coverage.xml", command)
        self.assertIn("--cov-fail-under=75.0", command)

    def test_detect_runner_uses_trace_when_pytest_cov_missing(self) -> None:
        repo_root = self.tmp_path / "sample_python_repo"
        repo_root.mkdir(parents=True)
        (repo_root / "pyproject.toml").write_text("[project]\nname='sample-python-repo'\nversion='0.0.1'\n", encoding="utf-8")

        def module_available(_python_exec: str, module_name: str) -> bool:
            return module_name == "pytest"

        with mock.patch.object(repo_health_check, "_python_candidates", return_value=["/tmp/python-under-test"]):
            with mock.patch.object(repo_health_check, "_module_available", side_effect=module_available):
                runner, command = repo_health_check.detect_runner(repo_root, min_coverage=75.0)
        self.assertEqual(runner, "python")
        self.assertEqual(command[:3], ["/tmp/python-under-test", "-m", "trace"])
        self.assertIn(".repo_health_trace_counts.pkl", " ".join(command))


class TestEcosystemCompletionGates(unittest.TestCase):
    """Tests for repo-specific completion gate enforcement (ECO-651)."""

    def test_get_extra_gates_ecosystem(self) -> None:
        extras = dispatcher_module._get_extra_gates_for_repo("/home/user/projects/ecosystem")
        self.assertEqual(extras, dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES)

    def test_get_extra_gates_non_ecosystem(self) -> None:
        extras = dispatcher_module._get_extra_gates_for_repo("/home/user/projects/CENTRAL")
        self.assertEqual(extras, ())

    def test_get_extra_gates_none(self) -> None:
        self.assertEqual(dispatcher_module._get_extra_gates_for_repo(None), ())

    def test_get_extra_gates_empty_string(self) -> None:
        self.assertEqual(dispatcher_module._get_extra_gates_for_repo(""), ())

    def test_match_gate_with_extra_required(self) -> None:
        extra = ("frontend unit tests", "cargo test lib")
        self.assertEqual(
            dispatcher_module._match_required_completion_gate("frontend unit tests", extra),
            "frontend unit tests",
        )
        self.assertEqual(
            dispatcher_module._match_required_completion_gate("cargo test lib", extra),
            "cargo test lib",
        )
        # Base gates still match
        self.assertEqual(
            dispatcher_module._match_required_completion_gate("cargo build", extra),
            "cargo build",
        )

    def test_collect_evidence_extra_required_present_passing(self) -> None:
        extra = dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES
        entries = [
            {"name": "cargo build", "passed": True, "notes": "ok"},
            {"name": "git commit", "passed": True, "notes": "abc1234"},
            {"name": "frontend unit tests", "passed": True, "notes": "all pass"},
            {"name": "cargo test lib", "passed": True, "notes": "all pass"},
        ]
        evidence, failures = dispatcher_module._collect_completion_gate_evidence(entries, extra)
        self.assertEqual(failures, [])
        self.assertTrue(evidence["frontend unit tests"]["present"])
        self.assertTrue(evidence["frontend unit tests"]["passed"])
        self.assertTrue(evidence["cargo test lib"]["present"])
        self.assertTrue(evidence["cargo test lib"]["passed"])

    def test_collect_evidence_extra_required_missing(self) -> None:
        extra = dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES
        entries = [
            {"name": "cargo build", "passed": True, "notes": "ok"},
            {"name": "git commit", "passed": True, "notes": "abc1234"},
            # frontend unit tests and cargo test lib intentionally omitted
        ]
        evidence, failures = dispatcher_module._collect_completion_gate_evidence(entries, extra)
        self.assertIn("required validation missing: frontend unit tests", failures)
        self.assertIn("required validation missing: cargo test lib", failures)

    def test_collect_evidence_extra_required_failed(self) -> None:
        extra = dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES
        entries = [
            {"name": "cargo build", "passed": True, "notes": "ok"},
            {"name": "git commit", "passed": True, "notes": "abc1234"},
            {"name": "frontend unit tests", "passed": False, "notes": "2 failures"},
            {"name": "cargo test lib", "passed": True, "notes": "ok"},
        ]
        evidence, failures = dispatcher_module._collect_completion_gate_evidence(entries, extra)
        self.assertIn("validation failed: frontend unit tests", failures)
        self.assertNotIn("validation failed: cargo test lib", failures)

    def test_extract_gate_status_ecosystem_all_pass(self) -> None:
        extra = dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES
        entries = [
            {"name": "cargo build", "passed": True, "notes": "ok"},
            {"name": "git commit", "passed": True, "notes": "abc1234"},
            {"name": "frontend unit tests", "passed": True, "notes": "ok"},
            {"name": "cargo test lib", "passed": True, "notes": "ok"},
        ]
        ok, _, failures = dispatcher_module._extract_completion_gate_status(entries, extra)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_extract_gate_status_ecosystem_missing_unit_tests(self) -> None:
        extra = dispatcher_module._ECOSYSTEM_EXTRA_GATE_NAMES
        entries = [
            {"name": "cargo build", "passed": True, "notes": "ok"},
            {"name": "git commit", "passed": True, "notes": "abc1234"},
        ]
        ok, _, failures = dispatcher_module._extract_completion_gate_status(entries, extra)
        self.assertFalse(ok)
        self.assertEqual(len(failures), 2)

    def test_worker_prompt_includes_ecosystem_unit_test_gates(self) -> None:
        snapshot = {
            "task_id": "ECO-TEST-1",
            "title": "Test task",
            "target_repo_root": "/home/user/projects/ecosystem",
            "task_type": "feature",
            "objective_md": "Do a thing",
            "context_md": "",
            "scope_md": "",
            "deliverables_md": "- thing done",
            "acceptance_md": "",
            "testing_md": "",
            "dispatch_md": "repo=ecosystem",
            "closeout_md": "",
            "reconciliation_md": "",
            "execution": {"task_kind": "mutating", "metadata": {}},
            "metadata": {},
        }
        task = model_policy.build_worker_task(snapshot, "codex-mini", worker_mode="codex")
        prompt = task["prompt_body"]
        self.assertIn("npx vitest run --project unit", prompt)
        self.assertIn("frontend unit tests", prompt)
        self.assertIn("cargo test --lib", prompt)
        self.assertIn("cargo test lib", prompt)

    def test_worker_prompt_no_ecosystem_gates_for_other_repos(self) -> None:
        snapshot = {
            "task_id": "OTHER-1",
            "title": "Other repo task",
            "target_repo_root": "/home/user/projects/CENTRAL",
            "task_type": "feature",
            "objective_md": "Do a thing",
            "context_md": "",
            "scope_md": "",
            "deliverables_md": "- thing done",
            "acceptance_md": "",
            "testing_md": "",
            "dispatch_md": "repo=CENTRAL",
            "closeout_md": "",
            "reconciliation_md": "",
            "execution": {"task_kind": "mutating", "metadata": {}},
            "metadata": {},
        }
        task = model_policy.build_worker_task(snapshot, "codex-mini", worker_mode="codex")
        prompt = task["prompt_body"]
        self.assertNotIn("npx vitest run --project unit", prompt)
        self.assertNotIn("cargo test --lib", prompt)


if __name__ == "__main__":
    unittest.main()
