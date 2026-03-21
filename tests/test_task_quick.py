#!/usr/bin/env python3
"""Focused smokes for task_quick planner tooling."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_QUICK = REPO_ROOT / "scripts" / "task_quick.py"
TASK_DB = REPO_ROOT / "scripts" / "central_task_db.py"


class TaskQuickSmokeTest(unittest.TestCase):
    def run_db(self, db_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(TASK_DB), *args, "--db-path", str(db_path)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def run_task_quick(
        self,
        db_path: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CENTRAL_TASK_DB_PATH"] = str(db_path)
        result = subprocess.run(
            [sys.executable, str(TASK_QUICK), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            env=env,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_planner_ops_smoke_uses_unique_title_and_preserves_smoke_db(self) -> None:
        with tempfile.TemporaryDirectory(prefix="central_task_quick_") as tmpdir:
            db_path = Path(tmpdir) / "central_tasks.db"
            self.run_db(db_path, "init")
            self.run_db(
                db_path,
                "repo-upsert",
                "--repo-id",
                "CENTRAL",
                "--repo-root",
                str(REPO_ROOT),
                "--display-name",
                "CENTRAL",
            )

            title = "planner smoke unique 2026-03-20-xyz"
            self.run_task_quick(
                db_path,
                "--title",
                title,
                "--repo",
                "CENTRAL",
                "--template",
                "planner-ops",
                "--initiative",
                "task-quick-smoke-seed",
            )

            smoke = self.run_task_quick(
                db_path,
                "--title",
                title,
                "--repo",
                "CENTRAL",
                "--template",
                "planner-ops",
                "--planner-ops-smoke",
            )

            self.assertIn("Planner-ops preflight smoke: pass", smoke.stdout)
            smoke_db_line = next(
                line for line in smoke.stdout.splitlines() if line.strip().startswith("smoke_db:")
            )
            smoke_db_path = Path(smoke_db_line.split(":", 1)[1].strip())
            self.assertTrue(smoke_db_path.exists(), smoke.stdout)

            created_id_line = next(
                line for line in smoke.stdout.splitlines() if line.strip().startswith("created_id:")
            )
            created_id = created_id_line.split(":", 1)[1].strip()
            show = self.run_db(db_path, "task-show", "--task-id", created_id, check=False)
            self.assertNotEqual(show.returncode, 0, show.stdout)


if __name__ == "__main__":
    unittest.main()
