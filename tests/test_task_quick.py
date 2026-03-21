#!/usr/bin/env python3
"""Focused CLI coverage for task_quick planner workflows."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_QUICK = REPO_ROOT / "scripts" / "task_quick.py"
TASK_DB = REPO_ROOT / "scripts" / "central_task_db.py"


class TaskQuickCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_quick_")
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"
        self.run_db_cli("init")
        self.run_db_cli(
            "repo-upsert",
            "--repo-id",
            "CENTRAL",
            "--repo-root",
            str(REPO_ROOT),
            "--display-name",
            "CENTRAL",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_db_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(TASK_DB), args[0], "--db-path", str(self.db_path), *args[1:]],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def run_task_quick(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(TASK_QUICK), "--db-path", str(self.db_path), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_dry_run_reports_preflight_and_alpha_without_writing(self) -> None:
        result = self.run_task_quick(
            "--title",
            "Verify planner dry run",
            "--repo",
            "CENTRAL",
            "--template",
            "planner-ops",
            "--dry-run",
        )
        self.assertIn("Dry-run: Verify planner dry run", result.stdout)
        self.assertIn("preflight: none", result.stdout)
        self.assertIn("alpha:     alpha-", result.stdout)

        tasks = json.loads(self.run_db_cli("task-list", "--json").stdout)
        self.assertEqual(tasks, [])

    def test_planner_ops_smoke_uses_temp_copy_and_preserves_source_db(self) -> None:
        result = self.run_task_quick(
            "--title",
            "Verify planner smoke",
            "--repo",
            "CENTRAL",
            "--template",
            "planner-ops",
            "--planner-ops-smoke",
        )
        self.assertIn("Planner-ops preflight smoke 2: pass", result.stdout)
        self.assertIn("alpha:        alpha-", result.stdout)
        self.assertIn("smoke_db:", result.stdout)
        self.assertIn("state:        preflight + task-create validated in smoke DB", result.stdout)

        tasks = json.loads(self.run_db_cli("task-list", "--json").stdout)
        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
