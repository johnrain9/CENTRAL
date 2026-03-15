#!/usr/bin/env python3
"""Smoke tests for CENTRAL repo alias and lookup behavior."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DB_CLI = REPO_ROOT / "scripts" / "central_task_db.py"


def make_task_payload(*, task_id: str, target_repo_id: str, target_repo_root: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"{task_id} repo lookup smoke",
        "summary": "Validate repo lookup canonicalization.",
        "objective_md": "Ensure variant repo references resolve to canonical repo IDs.",
        "context_md": "Smoke coverage for CENTRAL-OPS-57.",
        "scope_md": "Lookup behavior only.",
        "deliverables_md": "- canonical repo targeting",
        "acceptance_md": "- variant repo names resolve cleanly",
        "testing_md": "- CLI smoke test",
        "dispatch_md": "Local CLI smoke only.",
        "closeout_md": "Inspection via JSON output.",
        "reconciliation_md": "CENTRAL DB remains canonical.",
        "planner_status": "todo",
        "priority": 1,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": target_repo_id,
        "target_repo_root": target_repo_root,
        "approval_required": False,
        "metadata": {"smoke": True},
        "execution": {
            "task_kind": "read_only",
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "additional_writable_dirs": [],
            "timeout_seconds": 60,
            "metadata": {},
        },
        "dependencies": [],
    }


class RepoLookupCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="central_repo_lookup_")
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "central_tasks.db"
        self.run_cli("init")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DB_CLI), args[0], "--db-path", str(self.db_path), *args[1:]],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"central_task_db {' '.join(args)} failed: {result.stderr or result.stdout}")
        return result

    def write_payload(self, payload: dict[str, object], name: str) -> Path:
        path = self.tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_repo_variants_resolve_to_canonical_repo_id(self) -> None:
        self.run_cli(
            "repo-upsert",
            "--repo-id",
            "MOTOHELPER",
            "--repo-root",
            "/home/cobra/motoHelper",
            "--display-name",
            "Moto Helper",
            "--alias",
            "moto-helper",
            "--alias",
            "moto helper app",
        )

        resolved = json.loads(self.run_cli("repo-resolve", "--repo", "moto helper", "--json").stdout)
        self.assertEqual(resolved["repo_id"], "MOTOHELPER")
        self.assertEqual(resolved["lookup"]["match_quality"], "normalized")
        self.assertIn("display_name", resolved["lookup"]["matched_by"])

        listed = json.loads(self.run_cli("repo-list", "--json").stdout)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["aliases"], ["moto helper app", "moto-helper"])

        payload_path = self.write_payload(
            make_task_payload(
                task_id="CENTRAL-OPS-5701",
                target_repo_id="moto-helper",
                target_repo_root="/home/cobra/MotoHelper",
            ),
            "task-create.json",
        )
        created = json.loads(self.run_cli("task-create", "--input", str(payload_path), "--json").stdout)
        self.assertEqual(created["target_repo_id"], "MOTOHELPER")
        self.assertEqual(created["target_repo_root"], "/home/cobra/motoHelper")

        tasks = json.loads(self.run_cli("task-list", "--repo-id", "moto helper", "--json").stdout)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["repo"], "MOTOHELPER")
        self.assertEqual(tasks[0]["task_id"], "CENTRAL-OPS-5701")

    def test_ambiguous_repo_reference_fails_explicitly(self) -> None:
        self.run_cli(
            "repo-upsert",
            "--repo-id",
            "FRONTEND-A",
            "--repo-root",
            "/tmp/frontend-a",
            "--display-name",
            "Frontend A",
            "--alias",
            "shared-frontend",
        )
        self.run_cli(
            "repo-upsert",
            "--repo-id",
            "FRONTEND-B",
            "--repo-root",
            "/tmp/frontend-b",
            "--display-name",
            "Frontend B",
            "--alias",
            "shared_frontend",
        )

        result = self.run_cli("repo-resolve", "--repo", "shared frontend", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous repo reference", result.stderr)
        self.assertIn("FRONTEND-A", result.stderr)
        self.assertIn("FRONTEND-B", result.stderr)


if __name__ == "__main__":
    unittest.main()
