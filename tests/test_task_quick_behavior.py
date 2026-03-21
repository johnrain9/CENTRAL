#!/usr/bin/env python3
"""Behavior tests for task_quick preflight and create orchestration."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import task_quick


class TaskQuickBehaviorTest(unittest.TestCase):
    def make_args(self, **overrides):
        base = {
            "title": "Create behavior test",
            "repo": "PHOTO_AUTO_TAGGING",
            "db_path": None,
            "series": None,
            "template": "feature",
            "priority": None,
            "task_type": None,
            "objective": None,
            "context": None,
            "scope": None,
            "deliverables": None,
            "acceptance": None,
            "testing": None,
            "reconciliation": None,
            "depends_on": None,
            "initiative": "one-off",
            "dry_run": False,
            "planner_ops_smoke": False,
            "novelty_rationale": None,
            "list_templates": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_ensure_unique_smoke_title_appends_marker_once(self) -> None:
        title = task_quick.ensure_unique_smoke_title("My task", "CENTRAL-OPS-1")
        self.assertIn(task_quick.SMOKE_TITLE_MARKER, title)
        self.assertIn("CENTRAL-OPS-1", title)
        self.assertEqual(task_quick.ensure_unique_smoke_title(title, "CENTRAL-OPS-1"), title)

    def test_attach_preflight_adds_override_for_overlap(self) -> None:
        scaffold = {"task_id": "CENTRAL-OPS-999"}
        pf = {
            "blocking_bucket": "strong_overlap",
            "classification_options": ["follow_on"],
            "override_kind": "strong_overlap_privileged",
            "preflight_token": "tok-123",
            "candidates": [
                {"candidate_id": "task:CENTRAL-OPS-1"},
                {"candidate_id": "cap:foo"},
            ],
            "_request": {"normalized_task_intent": {}},
        }
        payload = task_quick.attach_preflight(scaffold, pf, "distinct work")
        self.assertEqual(payload["preflight"]["classification"], "follow_on")
        self.assertEqual(payload["override"]["override_kind"], "strong_overlap_privileged")
        self.assertEqual(
            payload["override"]["acknowledged_candidate_ids"],
            ["task:CENTRAL-OPS-1", "cap:foo"],
        )

    def test_create_task_non_platform_repo_skips_preflight_and_creates(self) -> None:
        args = self.make_args(repo="PHOTO_AUTO_TAGGING", template="feature")

        calls: list[list[str]] = []

        def fake_run(cmd, stdin=None, db_path=None, env_overrides=None):
            del stdin, db_path, env_overrides
            calls.append(cmd)
            if "planner-new" in cmd:
                return {
                    "task_id": "CENTRAL-OPS-7001",
                    "title": "Create behavior test",
                    "summary": "Synthetic",
                    "objective_md": "obj",
                    "context_md": "ctx",
                    "scope_md": "scope",
                    "deliverables_md": "deliv",
                    "acceptance_md": "acc",
                    "testing_md": "test",
                    "dispatch_md": "dispatch",
                    "closeout_md": "close",
                    "reconciliation_md": "recon",
                    "planner_status": "todo",
                    "priority": 50,
                    "task_type": "feature",
                    "planner_owner": "planner/coordinator",
                    "worker_owner": None,
                    "target_repo_id": "PHOTO_AUTO_TAGGING",
                    "target_repo_root": "/tmp/repo",
                    "approval_required": False,
                    "initiative": "one-off",
                    "metadata": {},
                    "execution": {
                        "task_kind": "mutating",
                        "sandbox_mode": "workspace-write",
                        "approval_policy": "never",
                        "timeout_seconds": 60,
                        "additional_writable_dirs": [],
                        "metadata": {},
                    },
                    "dependencies": [],
                }
            if "task-create" in cmd:
                return {"task_id": "CENTRAL-OPS-7001"}
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.object(task_quick, "get_next_task_id", return_value="CENTRAL-OPS-7001"), mock.patch.object(
            task_quick, "run", side_effect=fake_run
        ) as run_mock, mock.patch("sys.stdout"):
            task_quick.create_task(args)

        self.assertTrue(any("--skip-preflight" in cmd for cmd in calls if "task-create" in cmd))
        self.assertEqual(run_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
