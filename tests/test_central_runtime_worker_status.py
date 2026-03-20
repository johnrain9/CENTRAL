#!/usr/bin/env python3
"""Focused tests for worker log observability in dispatcher status output."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime


class WorkerLogObservabilityTest(unittest.TestCase):
    def make_snapshot(self) -> dict[str, object]:
        return {
            "task_id": "CENTRAL-OPS-30-TEST",
            "lease": {
                "lease_acquired_at": "2026-03-18T10:00:00+00:00",
                "lease_expires_at": "2026-03-18T10:01:00+00:00",
            },
            "runtime": {"runtime_status": "running"},
        }

    def test_worker_log_signal_reports_growing_flat_and_stale(self) -> None:
        snapshot = self.make_snapshot()

        growing = central_runtime.worker_log_signal(
            snapshot,
            log_info={"exists": True, "age_seconds": 2.0, "size_bytes": 512},
            log_growth={"bytes_since_last_inspection": 128},
        )
        self.assertEqual(growing["state"], "growing")

        flat = central_runtime.worker_log_signal(
            snapshot,
            log_info={"exists": True, "age_seconds": 5.0, "size_bytes": 512},
            log_growth={"bytes_since_last_inspection": 0},
        )
        self.assertEqual(flat["state"], "flat")

        stale = central_runtime.worker_log_signal(
            snapshot,
            log_info={"exists": True, "age_seconds": 90.0, "size_bytes": 512},
            log_growth={"bytes_since_last_inspection": 0},
        )
        self.assertEqual(stale["state"], "stale")
        self.assertTrue(stale["stale"])

    def test_worker_status_text_includes_log_size_delta_and_signal(self) -> None:
        payload = {
            "summary": {
                "overall_status": "healthy",
                "headline": "Active workers show fresh heartbeat or log activity.",
                "active_count": 1,
                "healthy_count": 1,
                "low_activity_count": 0,
                "potentially_stuck_count": 0,
            },
            "active_workers": [
                {
                    "task_id": "CENTRAL-OPS-30-TEST",
                    "run_id": "run-123",
                    "runtime_status": "running",
                    "observed_state": "healthy",
                    "reason": "fresh heartbeat or log activity detected",
                    "heartbeat": {"age_seconds": 1.2},
                    "log": {
                        "age_seconds": 0.4,
                        "size_bytes": 1536,
                        "growth": {"bytes_since_last_inspection": 256},
                        "signal": {"state": "growing"},
                    },
                }
            ],
            "recent_workers": [],
        }

        rendered = central_runtime.worker_status_text(payload)

        self.assertIn("log_size=1.5KB", rendered)
        self.assertIn("log_delta=+256B", rendered)
        self.assertIn("log_signal=growing", rendered)


if __name__ == "__main__":
    unittest.main()
