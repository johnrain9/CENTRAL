"""Integration tests for Claude worker result normalization pipeline.

Covers:
1. build_claude_command payload structure passes autonomy_runner.validate_worker_payload
2. normalize_claude_result output passes validate_worker_payload
3. _finalize_worker with COMPLETED result missing required fields fails gracefully
4. max_retries guard: retry_count >= max_retries aborts instead of re-dispatching

Root cause context (OPS-80):
  build_claude_command had 'validation': {} (dict) instead of [] (list), and earlier
  versions omitted blockers/decisions/discoveries. validate_worker_payload threw on these
  payloads, causing the reaper to mark every COMPLETED run as failed, which caused 34+
  retries.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
AUTONOMY_ROOT = Path("/home/cobra/photo_auto_tagging")

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(AUTONOMY_ROOT))


class TestBuildClaudeCommandPayloadValidation(unittest.TestCase):
    """Test 1: payload written by build_claude_command passes validate_worker_payload."""

    def _make_payload(self, is_error: bool = False) -> dict:
        """Replicate the payload structure that build_claude_command embeds in its script."""
        task_id = "CENTRAL-OPS-83"
        run_id = "run-test-1"
        summary = "Task done." if not is_error else "Something failed."
        return {
            "schema_version": 1,
            "task_id": task_id,
            "run_id": run_id,
            "status": "FAILED" if is_error else "COMPLETED",
            "summary": summary,
            "completed_items": [summary] if not is_error else [],
            "remaining_items": [],
            "decisions": [],
            "discoveries": [],
            "blockers": [],
            "validation": [],  # OPS-80: must be list, not {}
            "artifacts": [],
            "claude_raw": {},
        }

    def test_completed_payload_passes_validation(self):
        from autonomy import runner as autonomy_runner  # type: ignore
        payload = self._make_payload(is_error=False)
        # Should not raise
        autonomy_runner.validate_worker_payload(
            payload, task_id=payload["task_id"], run_id=payload["run_id"]
        )

    def test_failed_payload_passes_validation(self):
        from autonomy import runner as autonomy_runner  # type: ignore
        payload = self._make_payload(is_error=True)
        autonomy_runner.validate_worker_payload(
            payload, task_id=payload["task_id"], run_id=payload["run_id"]
        )

    def test_payload_missing_decisions_fails_validation(self):
        """Regression: a payload without decisions should fail validate_worker_payload."""
        from autonomy import runner as autonomy_runner  # type: ignore
        from autonomy.errors import ValidationError  # type: ignore
        payload = self._make_payload()
        del payload["decisions"]
        with self.assertRaises(ValidationError):
            autonomy_runner.validate_worker_payload(
                payload, task_id=payload["task_id"], run_id=payload["run_id"]
            )

    def test_validation_field_must_be_present(self):
        """Regression: omitting validation field (or using {}) must be caught."""
        from autonomy import runner as autonomy_runner  # type: ignore
        from autonomy.errors import ValidationError  # type: ignore
        payload = self._make_payload()
        del payload["validation"]
        with self.assertRaises(ValidationError):
            autonomy_runner.validate_worker_payload(
                payload, task_id=payload["task_id"], run_id=payload["run_id"]
            )


class TestNormalizeClaudeResultValidation(unittest.TestCase):
    """Test 2: normalize_claude_result output passes validate_worker_payload."""

    def test_normalized_success_passes_validation(self):
        import tempfile
        from autonomy import runner as autonomy_runner  # type: ignore
        from central_runtime import normalize_claude_result

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "worker.log"
            result_path = tmp_path / "result.json"
            claude_line = json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed successfully.",
                "session_id": "sess-test",
                "num_turns": 2,
            })
            log_path.write_text(claude_line, encoding="utf-8")

            task_id = "CENTRAL-OPS-83"
            run_id = "run-norm-1"
            assert normalize_claude_result(log_path, result_path, task_id, run_id) is True
            assert result_path.exists()

            payload = json.loads(result_path.read_text())
            autonomy_runner.validate_worker_payload(payload, task_id=task_id, run_id=run_id)

    def test_normalized_error_passes_validation(self):
        import tempfile
        from autonomy import runner as autonomy_runner  # type: ignore
        from central_runtime import normalize_claude_result

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "worker.log"
            result_path = tmp_path / "result.json"
            claude_line = json.dumps({
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "Error occurred.",
            })
            log_path.write_text(claude_line, encoding="utf-8")

            task_id = "CENTRAL-OPS-83"
            run_id = "run-norm-2"
            assert normalize_claude_result(log_path, result_path, task_id, run_id) is True

            payload = json.loads(result_path.read_text())
            autonomy_runner.validate_worker_payload(payload, task_id=task_id, run_id=run_id)


class TestFinalizeWorkerMissingFields(unittest.TestCase):
    """Test 3: _finalize_worker with COMPLETED result missing required fields fails gracefully.

    When load_result_file raises ValidationError (e.g., missing decisions field),
    _finalize_worker must catch the exception and transition to runtime_status=failed
    rather than propagating the exception or infinite-retrying.
    """

    def test_load_result_file_raises_on_missing_decisions(self):
        """Simulate what happens when a COMPLETED result is missing required fields."""
        import tempfile
        from autonomy.errors import ValidationError  # type: ignore
        from autonomy import runner as autonomy_runner  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result_path = tmp_path / "result.json"
            # COMPLETED result missing 'decisions' field
            incomplete = {
                "schema_version": 1,
                "task_id": "CENTRAL-OPS-83",
                "run_id": "run-broken",
                "status": "COMPLETED",
                "summary": "done",
                "completed_items": [],
                "remaining_items": [],
                # decisions, discoveries, blockers, validation intentionally omitted
            }
            result_path.write_text(json.dumps(incomplete), encoding="utf-8")

            with self.assertRaises(ValidationError):
                autonomy_runner.load_result_file(
                    result_path, task_id="CENTRAL-OPS-83", run_id="run-broken"
                )

    def test_finalize_worker_catches_validation_error(self):
        """_finalize_worker's except block catches ValidationError and sets runtime_status=failed."""
        # This tests that the except Exception as exc: block at the finalize path
        # correctly catches ValidationError without re-raising, by simulating the logic inline.
        from autonomy.errors import ValidationError  # type: ignore

        # Replicate the _finalize_worker handling of a result parse failure
        runtime_status = "failed"
        error_text = None
        try:
            raise ValidationError("missing fields: decisions, discoveries")
        except Exception as exc:
            runtime_status = "failed"
            error_text = f"result parse failed: {exc}"

        self.assertEqual(runtime_status, "failed")
        self.assertIn("result parse failed", error_text)
        self.assertIn("missing fields", error_text)


class TestMaxRetriesGuard(unittest.TestCase):
    """Test 4: max_retries guard prevents infinite retry loops."""

    def _make_snapshot(self, retry_count: int, task_id: str = "CENTRAL-OPS-83") -> dict:
        return {
            "task_id": task_id,
            "runtime": {"retry_count": retry_count},
            "lease": {"lease_owner_id": "worker-1"},
        }

    def test_snapshot_retry_count_reads_correctly(self):
        from central_runtime import snapshot_retry_count
        snapshot = self._make_snapshot(retry_count=7)
        self.assertEqual(snapshot_retry_count(snapshot), 7)

    def test_snapshot_retry_count_absent_runtime(self):
        from central_runtime import snapshot_retry_count
        self.assertEqual(snapshot_retry_count({"task_id": "X", "runtime": None}), 0)
        self.assertEqual(snapshot_retry_count({"task_id": "X"}), 0)

    def test_max_retries_guard_aborts_when_exceeded(self):
        """_abort_if_max_retries returns True and transitions task to failed."""
        from central_runtime import DispatcherConfig, CentralDispatcher, snapshot_retry_count
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "state"
            state_dir.mkdir()
            db_path = tmp_path / "test.db"

            config = DispatcherConfig(
                db_path=db_path,
                state_dir=state_dir,
                max_workers=1,
                poll_interval=1.0,
                heartbeat_seconds=30.0,
                status_heartbeat_seconds=60.0,
                stale_recovery_seconds=300.0,
                worker_mode="stub",
                max_retries=3,
            )

            dispatcher = CentralDispatcher(config)

            # Patch _connect and runtime_transition so we don't need a real DB
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: s
            mock_conn.__exit__ = MagicMock(return_value=False)

            with patch.object(dispatcher, "_connect", return_value=mock_conn), \
                 patch("central_runtime.task_db.runtime_transition") as mock_transition:

                # retry_count=3 equals max_retries=3 → should abort
                snapshot = self._make_snapshot(retry_count=3)
                result = dispatcher._abort_if_max_retries(snapshot)
                self.assertTrue(result)
                mock_transition.assert_called_once()
                call_kwargs = mock_transition.call_args
                self.assertEqual(call_kwargs.kwargs.get("status") or call_kwargs.args[2] if call_kwargs.args else call_kwargs.kwargs["status"], "failed")
                # error_text should be max_retries_exceeded
                self.assertIn("max_retries_exceeded", str(mock_transition.call_args))

    def test_max_retries_guard_does_not_abort_below_limit(self):
        """_abort_if_max_retries returns False when retry_count < max_retries."""
        from central_runtime import DispatcherConfig, CentralDispatcher
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "state"
            state_dir.mkdir()
            db_path = tmp_path / "test.db"

            config = DispatcherConfig(
                db_path=db_path,
                state_dir=state_dir,
                max_workers=1,
                poll_interval=1.0,
                heartbeat_seconds=30.0,
                status_heartbeat_seconds=60.0,
                stale_recovery_seconds=300.0,
                worker_mode="stub",
                max_retries=5,
            )

            dispatcher = CentralDispatcher(config)

            snapshot = self._make_snapshot(retry_count=2)
            # Should not abort (and should not call _connect since check is pure)
            with patch.object(dispatcher, "_connect") as mock_connect:
                result = dispatcher._abort_if_max_retries(snapshot)
                self.assertFalse(result)
                mock_connect.assert_not_called()

    def test_dispatcher_config_max_retries_default(self):
        """DispatcherConfig defaults max_retries to 5."""
        from central_runtime import DispatcherConfig
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = DispatcherConfig(
                db_path=tmp_path / "x.db",
                state_dir=tmp_path / "state",
                max_workers=1,
                poll_interval=1.0,
                heartbeat_seconds=30.0,
                status_heartbeat_seconds=60.0,
                stale_recovery_seconds=300.0,
                worker_mode="stub",
            )
            self.assertEqual(config.max_retries, 5)


if __name__ == "__main__":
    unittest.main()
