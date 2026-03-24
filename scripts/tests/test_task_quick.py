#!/usr/bin/env python3
"""Focused coverage for task_quick planner preflight flows."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "task_quick.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import central_task_db as task_db  # type: ignore
import task_quick  # type: ignore


def seed_task(task_id: str, title: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": title,
        "summary": f"Seeded task for {title}.",
        "objective_md": f"Validate {title}.",
        "context_md": "Synthetic task_quick smoke fixture.",
        "scope_md": "No persistent repo mutation.",
        "deliverables_md": "- verify smoke flow",
        "acceptance_md": "- smoke flow remains repeatable",
        "testing_md": "- automated",
        "dispatch_md": "No runtime dispatch.",
        "closeout_md": "Synthetic closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "planner-ops",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "CENTRAL",
        "target_repo_root": str(REPO_ROOT),
        "approval_required": False,
        "initiative": "one-off",
        "metadata": {"test_case": task_id},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 30,
            "metadata": {},
        },
        "dependencies": [],
    }


class TaskQuickPlannerSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_quick_")
        self.db_path = Path(self.tmpdir.name) / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
            with conn:
                task_db.ensure_repo(conn, repo_id="CENTRAL", repo_root=str(REPO_ROOT), display_name="CENTRAL")
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), "--db-path", str(self.db_path), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def fetch_titles(self) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return [row[0] for row in conn.execute("SELECT title FROM tasks ORDER BY task_id ASC").fetchall()]
        finally:
            conn.close()

    def test_planner_ops_smoke_is_repeatable_when_source_db_already_has_same_title(self) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.create_task(
                    conn,
                    seed_task("CENTRAL-OPS-7000", "Verify planner preflight smoke"),
                    actor_kind="test",
                    actor_id="task_quick.tests",
                )
        finally:
            conn.close()

        result = self.run_cli(
            "--title",
            "Verify planner preflight smoke",
            "--repo",
            "CENTRAL",
            "--template",
            "planner-ops",
            "--planner-ops-smoke",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Planner-ops preflight smoke: pass", result.stdout)
        self.assertIn("created_state:todo", result.stdout)
        self.assertIn("smoke_db:     ", result.stdout)

        titles = self.fetch_titles()
        self.assertEqual(titles, ["Verify planner preflight smoke"])
        self.assertFalse(any(task_quick.SMOKE_TITLE_MARKER in title for title in titles))

    def test_planner_ops_dry_run_defaults_to_one_off_initiative(self) -> None:
        result = self.run_cli(
            "--title",
            "Verify preflight integration",
            "--repo",
            "CENTRAL",
            "--template",
            "planner-ops",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Dry-run: Verify preflight integration", result.stdout)
        self.assertIn("state:     preflight validated, no write performed", result.stdout)

    def test_remote_task_marks_metadata(self) -> None:
        title = "Validate remote dispatch routing"
        result = self.run_cli(
            "--title",
            title,
            "--repo",
            "CENTRAL",
            "--template",
            "planner-ops",
            "--remote",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        created_id_line = next(
            (line for line in result.stdout.splitlines() if line.startswith("Created ")),
            "",
        )
        self.assertNotEqual(created_id_line, "")
        created_id = created_id_line.split(":", 1)[0].replace("Created ", "").strip()
        conn = task_db.connect(self.db_path)
        try:
            metadata = conn.execute(
                "SELECT metadata_json FROM tasks WHERE task_id = ?",
                (created_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(metadata)
        parsed_metadata = task_db.parse_json_text(
            str(metadata["metadata_json"]), default={}
        ) if metadata else {}
        self.assertTrue(parsed_metadata.get("remote") is True)
        self.assertTrue(parsed_metadata.get("remote_only") is True)


if __name__ == "__main__":
    unittest.main()
