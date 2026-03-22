#!/usr/bin/env python3
"""Behavior tests for central_runtime_v2.observation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from central_runtime_v2 import observation
from central_runtime_v2.config import RuntimePaths


def make_runtime_paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        state_dir=root,
        lock_path=root / "dispatcher.lock",
        log_path=root / "dispatcher.log",
        worker_status_cache_path=root / ".worker-status-cache.json",
        worker_logs_dir=root / ".worker-logs",
        worker_results_dir=root / ".worker-results",
        worker_prompts_dir=root / ".worker-prompts",
    )


class ObservationBehaviorTest(unittest.TestCase):
    def test_success_runtime_status_prefers_review_states(self) -> None:
        self.assertEqual(observation.success_runtime_status({"approval_required": True}), "pending_review")
        self.assertEqual(observation.success_runtime_status({"task_type": "truth"}), "pending_review")
        self.assertEqual(observation.success_runtime_status({"task_type": "implementation"}), "done")

    def test_timestamp_and_duration_helpers_handle_invalid_values(self) -> None:
        parsed = observation.parse_timestamp("2026-03-20T10:00:00Z")
        self.assertEqual(observation.iso_or_none(parsed), "2026-03-20T10:00:00+00:00")
        self.assertIsNone(observation.parse_timestamp("not-a-timestamp"))
        self.assertEqual(observation.age_seconds(datetime(2026, 3, 20, 10, 0, 5, tzinfo=timezone.utc), parsed), 5.0)
        self.assertEqual(
            observation.seconds_until(datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc), parsed),
            0.0,
        )
        self.assertEqual(observation.clamp(12.0, minimum=0.0, maximum=10.0), 10.0)

    def test_file_metadata_and_read_last_line_cover_missing_and_log_files(self) -> None:
        now = datetime(2026, 3, 20, 10, 0, 10, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_path = root / "worker.log"
            log_path.write_text("first\nsecond\nthird\n", encoding="utf-8")
            self.assertEqual(observation.read_last_line(log_path), "third")

            metadata = observation.file_metadata(log_path, now=now)
            self.assertTrue(metadata["exists"])
            self.assertEqual(metadata["last_line_preview"], "third")
            self.assertEqual(metadata["size_bytes"], log_path.stat().st_size)

            missing = observation.file_metadata(root / "missing.log", now=now)
            self.assertFalse(missing["exists"])
            self.assertEqual(missing["last_line_preview"], "")

            none_payload = observation.file_metadata(None, now=now)
            self.assertIsNone(none_payload["path"])
            self.assertFalse(none_payload["exists"])

    def test_status_cache_round_trip_trims_to_latest_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            workers = {
                f"worker-{index:03d}": {"observed_at": f"2026-03-20T10:{index:02d}:00+00:00", "size_bytes": index}
                for index in range(205)
            }
            observation.save_status_cache(cache_path, workers)
            loaded = observation.load_status_cache(cache_path)

        self.assertEqual(len(loaded), 200)
        self.assertIn("worker-204", loaded)
        self.assertNotIn("worker-000", loaded)
        self.assertEqual(observation.load_status_cache(Path("/tmp/does-not-exist.json")), {})

    def test_log_growth_and_event_helpers_report_previous_observation(self) -> None:
        growth, updated = observation.log_growth_payload(
            {"task-1:run-1": {"observed_at": "2026-03-20T10:00:00+00:00", "size_bytes": 120}},
            "task-1:run-1",
            {"path": "/tmp/run.log", "size_bytes": 180},
            observed_at="2026-03-20T10:01:00+00:00",
        )
        self.assertEqual(growth["bytes_since_last_inspection"], 60)
        self.assertEqual(growth["previous_observed_at"], "2026-03-20T10:00:00+00:00")
        self.assertEqual(updated["size_bytes"], 180)

        events = [
            {"event_type": "runtime.started", "created_at": "2026-03-20T10:00:00+00:00"},
            {"event_type": "runtime.heartbeat", "created_at": "2026-03-20T10:00:01+00:00"},
            {"event_type": "note", "created_at": "2026-03-20T10:00:02+00:00"},
        ]
        self.assertEqual(observation.latest_runtime_event(events), events[0])
        self.assertEqual(observation.latest_heartbeat_event(events), events[1])

    def test_artifact_path_selection_and_run_path_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = make_runtime_paths(root)
            for directory in (paths.worker_logs_dir, paths.worker_prompts_dir, paths.worker_results_dir):
                directory.mkdir(parents=True, exist_ok=True)
            log_artifact = {
                "artifact_kind": "log",
                "path_or_uri": str(root / "artifacts" / "run-2.log"),
                "created_at": "2026-03-20T10:02:00+00:00",
            }
            older_log = {
                "artifact_kind": "log",
                "path_or_uri": str(root / "artifacts" / "run-1.log"),
                "created_at": "2026-03-20T10:01:00+00:00",
            }
            prompt_artifact = {
                "artifact_kind": "prompt",
                "path_or_uri": str(root / "artifacts" / "run-2.md"),
                "created_at": "2026-03-20T10:02:00+00:00",
            }
            result_artifact = {
                "artifact_kind": "result",
                "path_or_uri": str(root / "artifacts" / "run-2.json"),
                "created_at": "2026-03-20T10:02:00+00:00",
            }
            artifacts = [older_log, log_artifact, prompt_artifact, result_artifact]

            selected = observation.select_latest_artifact_path(artifacts, ".log")
            inferred = observation.infer_recent_run_id("TASK-1", artifacts, paths)
            with_run = observation.worker_run_paths(paths, "TASK-1", "run-7", [])
            without_run = observation.worker_run_paths(paths, "TASK-1", None, artifacts)

        self.assertEqual(selected, Path(log_artifact["path_or_uri"]))
        self.assertEqual(inferred, "run-2")
        self.assertEqual(with_run["log"], paths.worker_logs_dir / "TASK-1" / "run-7.log")
        self.assertEqual(without_run["result"], Path(result_artifact["path_or_uri"]))

    def test_infer_recent_run_id_falls_back_to_worker_logs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = make_runtime_paths(root)
            task_logs_dir = paths.worker_logs_dir / "TASK-2"
            task_logs_dir.mkdir(parents=True, exist_ok=True)
            older = task_logs_dir / "older.log"
            newer = task_logs_dir / "newer.log"
            older.write_text("old\n", encoding="utf-8")
            newer.write_text("new\n", encoding="utf-8")
            older.touch()
            newer.touch()

            inferred = observation.infer_recent_run_id("TASK-2", [], paths)

        self.assertEqual(inferred, "newer")

    def test_classify_worker_run_and_log_signal_cover_active_and_terminal_states(self) -> None:
        claimed_snapshot = {
            "runtime": {"runtime_status": "claimed"},
            "lease": {
                "lease_acquired_at": "2026-03-20T10:00:00+00:00",
                "lease_expires_at": "2026-03-20T10:01:00+00:00",
            },
        }
        running_snapshot = {
            "runtime": {"runtime_status": "running"},
            "lease": {
                "lease_acquired_at": "2026-03-20T10:00:00+00:00",
                "lease_expires_at": "2026-03-20T10:02:00+00:00",
            },
        }
        self.assertEqual(
            observation.classify_worker_run(
                claimed_snapshot,
                heartbeat_age=None,
                seconds_to_lease_expiry=30.0,
                log_info={"age_seconds": 80.0},
                log_growth={"bytes_since_last_inspection": 0},
                runtime_event_age=None,
                transition_age=80.0,
            )[0],
            "potentially_stuck",
        )
        self.assertEqual(
            observation.classify_worker_run(
                running_snapshot,
                heartbeat_age=130.0,
                seconds_to_lease_expiry=30.0,
                log_info={"age_seconds": 130.0},
                log_growth={"bytes_since_last_inspection": 0},
                runtime_event_age=130.0,
                transition_age=10.0,
            )[0],
            "potentially_stuck",
        )
        self.assertEqual(
            observation.classify_worker_run(
                running_snapshot,
                heartbeat_age=2.0,
                seconds_to_lease_expiry=30.0,
                log_info={"age_seconds": 2.0},
                log_growth={"bytes_since_last_inspection": 5},
                runtime_event_age=2.0,
                transition_age=2.0,
            )[0],
            "healthy",
        )
        self.assertEqual(
            observation.classify_worker_run(
                running_snapshot,
                heartbeat_age=70.0,
                seconds_to_lease_expiry=30.0,
                log_info={"age_seconds": 70.0},
                log_growth={"bytes_since_last_inspection": 0},
                runtime_event_age=70.0,
                transition_age=10.0,
            )[0],
            "low_activity",
        )
        self.assertEqual(
            observation.classify_worker_run(
                {"runtime": {"runtime_status": "done"}, "lease": {}},
                heartbeat_age=None,
                seconds_to_lease_expiry=None,
                log_info={"age_seconds": None},
                log_growth={"bytes_since_last_inspection": None},
                runtime_event_age=None,
                transition_age=None,
            )[0],
            "recently_finished",
        )
        self.assertEqual(
            observation.classify_worker_run(
                {"runtime": {"runtime_status": "failed"}, "lease": {}},
                heartbeat_age=None,
                seconds_to_lease_expiry=None,
                log_info={"age_seconds": None},
                log_growth={"bytes_since_last_inspection": None},
                runtime_event_age=None,
                transition_age=None,
            )[0],
            "recent_issue",
        )
        self.assertEqual(
            observation.worker_log_signal(
                running_snapshot,
                log_info={"age_seconds": 80.0},
                log_growth={"bytes_since_last_inspection": 0},
            ),
            {"state": "stale", "stale": True},
        )

    def test_worker_status_text_and_validation_summary_render_expected_details(self) -> None:
        payload = {
            "summary": {
                "overall_status": "healthy",
                "headline": "Active workers show fresh heartbeat or log activity.",
                "active_count": 1,
                "healthy_count": 1,
                "low_activity_count": 0,
                "potentially_stuck_count": 0,
            },
            "runtime_paths": {"worker_results_dir": "/tmp/results"},
            "active_workers": [
                {
                    "task_id": "TASK-1",
                    "run_id": "run-123",
                    "runtime_status": "running",
                    "observed_state": "healthy",
                    "reason": "fresh heartbeat or log activity detected",
                    "heartbeat": {"age_seconds": 1.2},
                    "worker": {"model": "gpt-5.4"},
                    "log": {
                        "age_seconds": 0.4,
                        "size_bytes": 1536,
                        "growth": {"bytes_since_last_inspection": 256},
                        "signal": {"state": "growing"},
                    },
                }
            ],
            "recent_workers": [
                {
                    "task_id": "TASK-2",
                    "run_id": "run-456",
                    "runtime_status": "done",
                    "observed_state": "recently_finished",
                    "reason": "latest run reached a successful terminal state",
                    "worker": {"model": "gpt-5.3-codex"},
                    "runtime": {"finished_at": "2026-03-20T10:00:00+00:00", "last_transition_at": None},
                }
            ],
        }
        rendered = observation.worker_status_text(payload)
        summary = observation.summarize_validation_results(
            [
                {"name": "coverage", "passed": True, "notes": "met target"},
                {"name": "tests", "passed": False, "notes": "none"},
            ]
        )

        self.assertIn("Structured results: /tmp/results", rendered)
        self.assertIn("log_size=1.5KB", rendered)
        self.assertIn("Recent workers:", rendered)
        self.assertEqual(summary, "coverage: passed (met target); tests: failed (none)")
        self.assertIsNone(observation.summarize_validation_results([]))

    def test_add_artifacts_passes_labels_and_metadata_to_task_db(self) -> None:
        fake_conn = mock.MagicMock()
        artifacts = [("report", "/tmp/output/result.json", {"kind": "structured"})]
        with mock.patch("central_runtime_v2.observation.task_db.connect", return_value=fake_conn), mock.patch(
            "central_runtime_v2.observation.task_db.require_initialized_db"
        ), mock.patch("central_runtime_v2.observation.task_db.insert_artifact") as insert_artifact:
            observation.add_artifacts("TASK-99", artifacts, Path("/tmp/central.db"))

        insert_artifact.assert_called_once_with(
            fake_conn,
            task_id="TASK-99",
            artifact_kind="report",
            path_or_uri="/tmp/output/result.json",
            label="result.json",
            metadata={"kind": "structured"},
        )
        fake_conn.close.assert_called_once()

    def test_load_status_cache_returns_empty_for_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text("{invalid", encoding="utf-8")
            self.assertEqual(observation.load_status_cache(cache_path), {})
            cache_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
            self.assertEqual(observation.load_status_cache(cache_path), {})

    def test_read_last_line_returns_empty_for_blank_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.log"
            path.write_text("   \n\n   \n", encoding="utf-8")
            # All lines are whitespace-only so stripped lines list is empty
            self.assertEqual(observation.read_last_line(path), "")

    def test_infer_recent_run_id_returns_none_when_logs_dir_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = make_runtime_paths(root)
            empty_task_logs_dir = paths.worker_logs_dir / "TASK-EMPTY"
            empty_task_logs_dir.mkdir(parents=True, exist_ok=True)
            result = observation.infer_recent_run_id("TASK-EMPTY", [], paths)
            self.assertIsNone(result)

    def test_latest_heartbeat_event_returns_none_when_absent(self) -> None:
        events = [
            {"event_type": "runtime.started", "created_at": "2026-03-20T10:00:00+00:00"},
            {"event_type": "note", "created_at": "2026-03-20T10:00:01+00:00"},
        ]
        self.assertIsNone(observation.latest_heartbeat_event(events))
        self.assertIsNone(observation.latest_heartbeat_event([]))

    def test_classify_worker_run_lease_expired_returns_potentially_stuck(self) -> None:
        snapshot = {
            "runtime": {"runtime_status": "running"},
            "lease": {
                "lease_acquired_at": "2026-03-20T10:00:00+00:00",
                "lease_expires_at": "2026-03-20T10:01:00+00:00",
            },
        }
        state, reason = observation.classify_worker_run(
            snapshot,
            heartbeat_age=5.0,
            seconds_to_lease_expiry=-1.0,  # expired
            log_info={"age_seconds": 5.0},
            log_growth={"bytes_since_last_inspection": 0},
            runtime_event_age=5.0,
            transition_age=5.0,
        )
        self.assertEqual(state, "potentially_stuck")
        self.assertIn("expired", reason)

    def test_classify_worker_run_returns_idle_for_unknown_status(self) -> None:
        snapshot = {"runtime": {"runtime_status": "queued"}, "lease": {}}
        state, reason = observation.classify_worker_run(
            snapshot,
            heartbeat_age=None,
            seconds_to_lease_expiry=None,
            log_info={"age_seconds": None},
            log_growth={"bytes_since_last_inspection": None},
            runtime_event_age=None,
            transition_age=None,
        )
        self.assertEqual(state, "idle")

    def test_worker_status_text_byte_formatting_and_edge_case_paths(self) -> None:
        base_payload = {
            "summary": {
                "overall_status": "idle",
                "headline": "No active workers.",
                "active_count": 0,
                "healthy_count": 0,
                "low_activity_count": 0,
                "potentially_stuck_count": 0,
            },
            "runtime_paths": {},  # no worker_results_dir key
            "active_workers": [],
            "recent_workers": [],
        }
        rendered = observation.worker_status_text(base_payload)
        self.assertIn("- no active workers", rendered)
        self.assertNotIn("Recent workers:", rendered)

        # Test MB formatting (>= 1MB log size)
        mb_payload = {
            **base_payload,
            "summary": {**base_payload["summary"], "active_count": 1, "healthy_count": 1},
            "active_workers": [
                {
                    "task_id": "TASK-BIG",
                    "run_id": "run-big",
                    "runtime_status": "running",
                    "observed_state": "healthy",
                    "reason": "active",
                    "heartbeat": {"age_seconds": 1.0},
                    "worker": {"model": "gpt-5"},
                    "log": {
                        "size_bytes": 2 * 1024 * 1024,
                        "growth": {"bytes_since_last_inspection": 0},
                        "signal": {"state": "flat"},
                    },
                }
            ],
            "recent_workers": [],
        }
        mb_rendered = observation.worker_status_text(mb_payload)
        self.assertIn("2.0MB", mb_rendered)

        # Test bare bytes formatting (< 1KB)
        byte_payload = {
            **base_payload,
            "summary": {**base_payload["summary"], "active_count": 1, "healthy_count": 1},
            "active_workers": [
                {
                    "task_id": "TASK-TINY",
                    "run_id": "run-tiny",
                    "runtime_status": "running",
                    "observed_state": "healthy",
                    "reason": "active",
                    "heartbeat": {"age_seconds": None},
                    "worker": {"model": None},
                    "log": {
                        "size_bytes": 512,
                        "growth": {"bytes_since_last_inspection": None},
                        "signal": {"state": "flat"},
                    },
                }
            ],
            "recent_workers": [],
        }
        byte_rendered = observation.worker_status_text(byte_payload)
        self.assertIn("512B", byte_rendered)


if __name__ == "__main__":
    unittest.main()
