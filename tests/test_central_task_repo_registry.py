#!/usr/bin/env python3
"""Tests for registry-first planner repo targeting."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "central_task_db.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_task_db as task_db


def task_payload(task_id: str, *, target_repo_id: str, target_repo_root: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} repo registry test",
        "summary": "Exercise registry-first planner task validation.",
        "objective_md": "Create a task through the canonical registry.",
        "context_md": "Temporary DB only.",
        "scope_md": "No repo mutation required.",
        "deliverables_md": "- one canonical task row",
        "acceptance_md": "- planner task targets a registered canonical repo",
        "testing_md": "- automated unittest coverage only",
        "dispatch_md": "Dispatch locally only after repo onboarding succeeds.",
        "closeout_md": "Synthetic closeout only.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": target_repo_id,
        "target_repo_root": target_repo_root,
        "approval_required": False,
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


class CentralTaskRepoRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_task_repo_registry_")
        tmp_path = Path(self.tmpdir.name)
        self.db_path = tmp_path / "central_tasks.db"
        conn = task_db.connect(self.db_path)
        try:
            task_db.apply_migrations(conn, task_db.load_migrations(task_db.resolve_migrations_dir(None)))
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def onboard_repo(self, repo_id: str, repo_root: str, *, aliases: list[str] | None = None) -> None:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                task_db.ensure_repo(
                    conn,
                    repo_id=repo_id,
                    repo_root=repo_root,
                    display_name=repo_id,
                )
                if aliases is not None:
                    task_db.replace_repo_aliases(conn, repo_id=repo_id, aliases=aliases)
        finally:
            conn.close()

    def create_task(self, payload: dict[str, object]) -> dict[str, object]:
        conn = task_db.connect(self.db_path)
        try:
            with conn:
                return task_db.create_task(conn, payload, actor_kind="test", actor_id="central.repo.registry.tests")
        finally:
            conn.close()

    def test_registered_alias_is_canonicalized_for_task_identity(self) -> None:
        repo_root = str(Path(self.tmpdir.name) / "portfolio" / "photo_auto_tagging")
        self.onboard_repo("PHOTO_AUTO_TAGGING", repo_root, aliases=["photo-auto-tagging"])

        snapshot = self.create_task(
            task_payload(
                "CENTRAL-OPS-9600",
                target_repo_id="photo-auto-tagging",
                target_repo_root=repo_root,
            )
        )

        self.assertEqual(snapshot["target_repo_id"], "PHOTO_AUTO_TAGGING")
        self.assertEqual(snapshot["target_repo_root"], repo_root)

    def test_task_create_cli_fails_fast_for_unregistered_repo(self) -> None:
        self.onboard_repo("CENTRAL", str(REPO_ROOT))
        payload_path = Path(self.tmpdir.name) / "unregistered-task.json"
        payload_path.write_text(
            json.dumps(
                task_payload(
                    "CENTRAL-OPS-9601",
                    target_repo_id="motoHelper",
                    target_repo_root="/tmp/motoHelper",
                )
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "task-create",
                "--db-path",
                str(self.db_path),
                "--input",
                str(payload_path),
            ],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repo onboarding required before planner task creation/update.", result.stderr)
        self.assertIn("repo-onboard --repo-id motoHelper --repo-root /tmp/motoHelper", result.stderr)
        self.assertIn("Known repo_ids: CENTRAL", result.stderr)

    def test_task_update_rejects_unregistered_repo_change(self) -> None:
        self.onboard_repo("CENTRAL", str(REPO_ROOT))
        self.create_task(
            task_payload(
                "CENTRAL-OPS-9602",
                target_repo_id="CENTRAL",
                target_repo_root=str(REPO_ROOT),
            )
        )

        stderr = io.StringIO()
        conn = task_db.connect(self.db_path)
        try:
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                with conn:
                    task_db.update_task(
                        conn,
                        task_id="CENTRAL-OPS-9602",
                        expected_version=1,
                        payload={
                            "target_repo_id": "motoHelper",
                            "target_repo_root": "/tmp/motoHelper",
                        },
                        actor_kind="planner",
                        actor_id="central.repo.registry.tests",
                        allow_active_lease=False,
                    )
        finally:
            conn.close()

        message = stderr.getvalue()
        self.assertIn("repo onboarding required before planner task creation/update.", message)
        self.assertIn("target repo is not registered", message)
        self.assertIn("repo-onboard --repo-id motoHelper --repo-root /tmp/motoHelper", message)


if __name__ == "__main__":
    unittest.main()
