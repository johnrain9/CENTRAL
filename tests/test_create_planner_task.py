#!/usr/bin/env python3
"""Smokes for the AI-facing planner task creation helper."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CREATE_TASK = REPO_ROOT / "scripts" / "create_planner_task.py"
TASK_DB = REPO_ROOT / "scripts" / "central_task_db.py"


class CreatePlannerTaskTest(unittest.TestCase):
    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CREATE_TASK), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def run_db_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(TASK_DB), *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_help_documents_required_optional_and_presets(self) -> None:
        result = self.run_cli("--help")
        self.assertIn("Required content fields:", result.stdout)
        self.assertIn("Optional with defaults:", result.stdout)
        self.assertIn("--preset", result.stdout)
        self.assertIn("--audit-mode", result.stdout)
        self.assertIn("--preview-graph", result.stdout)
        self.assertIn("--depends-on", result.stdout)
        self.assertIn("--backfill", result.stdout)
        self.assertIn("--landed-ref", result.stdout)

    def test_preview_graph_shows_defaulted_sections_and_linked_audit(self) -> None:
        result = self.run_cli(
            "--preview-graph",
            "--task-id",
            "CENTRAL-OPS-3501",
            "--title",
            "Improve task creation UX",
            "--objective",
            "Reduce repetitive boilerplate for AI planners.",
            "--context-item",
            "The canonical task schema must remain rich.",
            "--scope-item",
            "Change CENTRAL task creation tooling only.",
            "--deliverable",
            "Improved AI-facing task creation helper.",
            "--deliverable",
            "Focused task-creation smokes.",
            "--acceptance-item",
            "AI can create a rich canonical task with less repetitive input.",
            "--test",
            "python3 -m unittest tests.test_create_planner_task",
        )
        payload = json.loads(result.stdout)
        parent = payload["parent"]
        audit = payload["audit"]

        self.assertEqual(parent["summary"], "Improve task creation UX")
        self.assertTrue(parent["metadata"]["audit_required"])
        self.assertEqual(parent["task_type"], "implementation")
        self.assertEqual(parent["execution"]["task_kind"], "mutating")
        self.assertIn("runtime contract", parent["dispatch_md"])
        self.assertIn("remaining risks", parent["closeout_md"])
        self.assertIn("paired audit", parent["reconciliation_md"])
        self.assertEqual(parent["deliverables_md"], "- Improved AI-facing task creation helper.\n- Focused task-creation smokes.")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["task_id"], "CENTRAL-OPS-3501-AUDIT")
        self.assertEqual(audit["dependencies"], ["CENTRAL-OPS-3501"])
        self.assertIn("reproduce the original bug", audit["testing_md"])
        self.assertIn("do not pass by default", audit["closeout_md"])

    def test_create_supports_audit_opt_out_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="central_create_task_") as tmpdir:
            db_path = Path(tmpdir) / "central_tasks.db"
            self.run_db_cli("init", "--db-path", str(db_path))
            self.run_db_cli(
                "repo-upsert",
                "--db-path",
                str(db_path),
                "--repo-id",
                "CENTRAL",
                "--repo-root",
                str(REPO_ROOT),
                "--display-name",
                "CENTRAL",
            )
            for dep_task_id in ("CENTRAL-OPS-1000", "CENTRAL-OPS-1001"):
                self.run_cli(
                    "--db-path",
                    str(db_path),
                    "--task-id",
                    dep_task_id,
                    "--title",
                    f"Dependency {dep_task_id}",
                    "--objective",
                    "Provide an existing dependency task for the smoke test.",
                    "--context-item",
                    "Used only to satisfy foreign-key dependency requirements.",
                    "--scope-item",
                    "Smoke test only.",
                    "--deliverable",
                    "A pre-existing dependency task.",
                    "--acceptance-item",
                    "Another task can depend on this task.",
                    "--test",
                    "smoke only",
                    "--audit-mode",
                    "none",
                )
            result = self.run_cli(
                "--db-path",
                str(db_path),
                "--task-id",
                "CENTRAL-OPS-3502",
                "--title",
                "Create no-audit task",
                "--objective",
                "Create a canonical task without a paired audit.",
                "--context-item",
                "Used only by the planner task creation smoke test.",
                "--scope-item",
                "CENTRAL task creation flow only.",
                "--deliverable",
                "One implementation task without an audit child.",
                "--acceptance-item",
                "Task is created with dependency metadata preserved.",
                "--test",
                "python3 -m unittest tests.test_create_planner_task",
                "--depends-on",
                "CENTRAL-OPS-1000",
                "--depends-on",
                "CENTRAL-OPS-1001",
                "--audit-mode",
                "none",
                "--json",
            )
            created = json.loads(result.stdout)
            self.assertEqual(created["task_id"], "CENTRAL-OPS-3502")
            self.assertFalse(created["metadata"]["audit_required"])
            self.assertNotIn("child_audit_task_id", created["metadata"])
            self.assertEqual(
                [row["depends_on_task_id"] for row in created["dependencies"]],
                ["CENTRAL-OPS-1000", "CENTRAL-OPS-1001"],
            )

            show = self.run_db_cli("task-show", "--db-path", str(db_path), "--task-id", "CENTRAL-OPS-3502-AUDIT", "--json", check=False)
            self.assertNotEqual(show.returncode, 0)

    def test_backfill_preview_marks_parent_audit_ready_and_carries_landed_refs(self) -> None:
        result = self.run_cli(
            "--preview-graph",
            "--task-id",
            "CENTRAL-OPS-3504",
            "--title",
            "Backfill landed task",
            "--objective",
            "Capture already-landed work in canonical CENTRAL task history.",
            "--context-item",
            "The implementation merged before a canonical task existed.",
            "--scope-item",
            "Task creation workflow only.",
            "--deliverable",
            "Backfilled implementation record.",
            "--acceptance-item",
            "Independent audit can inspect the landed change directly.",
            "--test",
            "bash tests/test_central_backfill_flow.sh",
            "--backfill",
            "--landed-ref",
            "commit:abc123",
            "--landed-ref",
            "pr:https://example.invalid/123",
            "--backfill-reason",
            "Fast-path work landed before canonical task creation.",
            "--audit-focus",
            "Verify the landed diff matches the stated scope.",
        )
        payload = json.loads(result.stdout)
        parent = payload["parent"]
        audit = payload["audit"]

        self.assertEqual(parent["planner_status"], "awaiting_audit")
        self.assertEqual(parent["metadata"]["workflow_kind"], "backfill")
        self.assertEqual(
            parent["metadata"]["backfill_landed_refs"],
            ["commit:abc123", "pr:https://example.invalid/123"],
        )
        self.assertEqual(parent["metadata"]["closeout"]["outcome"], "awaiting_audit")
        self.assertIn("Do not dispatch implementation work", parent["dispatch_md"])
        self.assertIsNotNone(audit)
        self.assertIn("already landed", audit["dispatch_md"])
        self.assertIn("Focused audit expectations", audit["context_md"])
        self.assertIn("commit:abc123", audit["context_md"])
        self.assertIn("reproduce the original bug", audit["testing_md"])
        self.assertIn("do not pass by default", audit["closeout_md"])

    def test_light_audit_preview_requires_bug_reproduction_before_passing(self) -> None:
        result = self.run_cli(
            "--preview-graph",
            "--task-id",
            "CENTRAL-OPS-3506",
            "--title",
            "Fix bounded regression",
            "--objective",
            "Fix a small regression without changing broader planner behavior.",
            "--context-item",
            "Bug report: clicking the status bell opens search instead of alerts.",
            "--scope-item",
            "Bounded planner UI fix only.",
            "--deliverable",
            "Regression fix.",
            "--acceptance-item",
            "Bell opens alerts instead of search.",
            "--test",
            "python3 -m unittest tests.test_create_planner_task",
            "--audit-mode",
            "light",
        )
        payload = json.loads(result.stdout)
        audit = payload["audit"]

        self.assertIsNotNone(audit)
        self.assertIn("reproduce the original bug", audit["acceptance_md"])
        self.assertIn("do not pass by default", audit["testing_md"])
        self.assertIn("confirmed it no longer occurs", audit["closeout_md"])

    def test_backfill_create_makes_parent_non_dispatchable_and_audit_immediately_eligible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="central_backfill_task_") as tmpdir:
            db_path = Path(tmpdir) / "central_tasks.db"
            self.run_db_cli("init", "--db-path", str(db_path))
            self.run_db_cli(
                "repo-upsert",
                "--db-path",
                str(db_path),
                "--repo-id",
                "CENTRAL",
                "--repo-root",
                str(REPO_ROOT),
                "--display-name",
                "CENTRAL",
            )
            created = json.loads(
                self.run_cli(
                    "--db-path",
                    str(db_path),
                    "--task-id",
                    "CENTRAL-OPS-3505",
                    "--title",
                    "Backfill landed CENTRAL change",
                    "--objective",
                    "Capture an already-landed change without inventing new implementation dispatch.",
                    "--context-item",
                    "The code is already merged.",
                    "--scope-item",
                    "Planner tooling only.",
                    "--deliverable",
                    "Canonical task record.",
                    "--acceptance-item",
                    "Paired audit starts immediately.",
                    "--test",
                    "bash tests/test_central_backfill_flow.sh",
                    "--backfill",
                    "--landed-ref",
                    "commit:def456",
                    "--json",
                ).stdout
            )
            self.assertEqual(created["planner_status"], "awaiting_audit")
            self.assertEqual(created["metadata"]["workflow_kind"], "backfill")
            self.assertIsNone(created["runtime_status"])

            audit = json.loads(
                self.run_db_cli(
                    "task-show",
                    "--db-path",
                    str(db_path),
                    "--task-id",
                    "CENTRAL-OPS-3505-AUDIT",
                    "--json",
                ).stdout
            )
            eligible = json.loads(self.run_db_cli("view-eligible", "--db-path", str(db_path), "--json").stdout)

            self.assertEqual(audit["planner_status"], "todo")
            self.assertEqual(audit["dependencies"][0]["depends_on_task_id"], "CENTRAL-OPS-3505")
            self.assertEqual([row["task_id"] for row in eligible], ["CENTRAL-OPS-3505-AUDIT"])

    def test_planning_preset_defaults_to_read_only_without_audit(self) -> None:
        result = self.run_cli(
            "--dry-run",
            "--preset",
            "planning",
            "--task-id",
            "CENTRAL-OPS-3503",
            "--title",
            "Plan task creation changes",
            "--objective",
            "Outline the required task-creation improvements.",
            "--context-item",
            "This is a planner-only task.",
            "--scope-item",
            "No repo mutation beyond planning artifacts.",
            "--deliverable",
            "A plan with sequencing and validation.",
            "--acceptance-item",
            "The plan is actionable and scoped.",
            "--test",
            "manual review only",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_type"], "planning")
        self.assertEqual(payload["execution"]["task_kind"], "read_only")
        self.assertFalse(payload["metadata"]["audit_required"])
        self.assertIn("Planner may close directly", payload["reconciliation_md"])


if __name__ == "__main__":
    unittest.main()
