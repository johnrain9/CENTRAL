#!/usr/bin/env python3
"""Tests for portfolio_report.py — manager-friendly portfolio status."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_REPORT = REPO_ROOT / "scripts" / "portfolio_report.py"
DB_CLI = REPO_ROOT / "scripts" / "central_task_db.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import portfolio_report


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PORTFOLIO_REPORT), *args],
        capture_output=True,
        text=True,
    )


def _make_db(tmp_dir: str) -> Path:
    """Create a minimal initialized DB with a repo and tasks for testing."""
    db_path = Path(tmp_dir) / "test.db"

    def cli(*args: str) -> None:
        r = subprocess.run(
            [sys.executable, str(DB_CLI), "--db-path", str(db_path), *args],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"DB CLI failed: {r.stderr[:400]}")

    cli("init")
    cli(
        "repo-onboard",
        "--repo-id", "TESTREPO",
        "--repo-root", tmp_dir,
    )

    base_task = {
        "planner_status": "todo",
        "priority": 2,
        "task_type": "implementation",
        "planner_owner": "planner/coordinator",
        "worker_owner": None,
        "target_repo_id": "TESTREPO",
        "target_repo_root": tmp_dir,
        "approval_required": False,
        "metadata": {},
        "summary": "Test task summary.",
        "objective_md": "Objective.",
        "context_md": "Context.",
        "scope_md": "Scope.",
        "deliverables_md": "- deliverable",
        "acceptance_md": "- accepted",
        "testing_md": "- test",
        "dispatch_md": "Dispatch.",
        "closeout_md": "Task shipped successfully.",
        "reconciliation_md": "Reconciled.",
    }

    tasks = [
        {**base_task, "task_id": "TEST-OPS-1", "title": "Alpha task", "initiative": "alpha-initiative", "planner_status": "done"},
        {**base_task, "task_id": "TEST-OPS-2", "title": "Beta task", "initiative": "alpha-initiative", "planner_status": "todo"},
        {**base_task, "task_id": "TEST-OPS-3", "title": "Gamma task", "initiative": "beta-initiative", "planner_status": "done"},
        {**base_task, "task_id": "TEST-OPS-4", "title": "Delta task", "initiative": None, "planner_status": "in_progress"},
    ]

    for task in tasks:
        payload = json.dumps(task)
        r = subprocess.run(
            [sys.executable, str(DB_CLI), "--db-path", str(db_path), "task-create", "--json-payload", payload],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"task-create failed for {task['task_id']}: {r.stderr[:400]}")

    return db_path


class TestBuildReport(unittest.TestCase):
    """Unit tests for build_report() with synthetic task data."""

    def _tasks(self) -> list[dict]:
        return [
            {"task_id": "X-1", "title": "T1", "summary": "s1", "planner_status": "done",
             "repo": "REPO_A", "initiative": "init-a", "priority": 1,
             "closeout_md": "Shipped the feature.", "closed_at": "2026-01-01T00:00:00+00:00",
             "runtime_status": "done", "last_runtime_error": None},
            {"task_id": "X-2", "title": "T2", "summary": "s2", "planner_status": "todo",
             "repo": "REPO_A", "initiative": "init-a", "priority": 2,
             "closeout_md": None, "closed_at": None,
             "runtime_status": None, "last_runtime_error": None},
            {"task_id": "X-3", "title": "T3", "summary": "s3", "planner_status": "todo",
             "repo": "REPO_B", "initiative": "init-b", "priority": 1,
             "closeout_md": None, "closed_at": None,
             "runtime_status": "failed", "last_runtime_error": "parse error"},
            {"task_id": "X-4", "title": "T4", "summary": "s4", "planner_status": "in_progress",
             "repo": "REPO_A", "initiative": None, "priority": 1,
             "closeout_md": None, "closed_at": None,
             "runtime_status": None, "last_runtime_error": None},
        ]

    def test_initiative_grouping(self):
        report = portfolio_report.build_report(self._tasks())
        names = [i["initiative"] for i in report["initiatives"]]
        self.assertIn("init-a", names)
        self.assertIn("init-b", names)
        self.assertIn("(untagged)", names)

    def test_totals(self):
        report = portfolio_report.build_report(self._tasks())
        self.assertEqual(report["totals"]["total"], 4)
        self.assertEqual(report["totals"]["done"], 1)
        self.assertEqual(report["totals"]["todo"], 1)  # X-2 (X-3 is failed)
        self.assertEqual(report["totals"]["in_progress"], 1)
        self.assertEqual(report["totals"]["failed"], 1)

    def test_done_task_uses_closeout(self):
        report = portfolio_report.build_report(self._tasks())
        init_a = next(i for i in report["initiatives"] if i["initiative"] == "init-a")
        repo_a = next(r for r in init_a["repos"] if r["repo"] == "REPO_A")
        done_task = next(t for t in repo_a["tasks"] if t["task_id"] == "X-1")
        self.assertEqual(done_task["summary"], "Shipped the feature.")

    def test_failed_task_uses_error(self):
        report = portfolio_report.build_report(self._tasks())
        init_b = next(i for i in report["initiatives"] if i["initiative"] == "init-b")
        repo_b = next(r for r in init_b["repos"] if r["repo"] == "REPO_B")
        failed_task = next(t for t in repo_b["tasks"] if t["task_id"] == "X-3")
        self.assertTrue(failed_task["failed"])
        self.assertEqual(failed_task["summary"], "parse error")

    def test_completion_pct(self):
        report = portfolio_report.build_report(self._tasks())
        # 1 done out of 4 = 25%
        self.assertEqual(report["completion_pct"], 25)

    def test_empty_tasks(self):
        report = portfolio_report.build_report([])
        self.assertEqual(report["totals"]["total"], 0)
        self.assertEqual(report["initiatives"], [])

    def test_planner_done_runtime_failed_not_marked_as_failed(self):
        """If planner=done but runtime=failed, treat as done (planner is authoritative)."""
        tasks = [
            {"task_id": "X-9", "title": "T9", "summary": "s", "planner_status": "done",
             "repo": "R", "initiative": "i", "priority": 1,
             "closeout_md": "Done.", "closed_at": "2026-01-01T00:00:00+00:00",
             "runtime_status": "failed", "last_runtime_error": "stale failure"},
        ]
        report = portfolio_report.build_report(tasks)
        task = report["initiatives"][0]["repos"][0]["tasks"][0]
        self.assertFalse(task["failed"])
        self.assertEqual(task["planner_status"], "done")


class TestRenderMarkdown(unittest.TestCase):
    def test_contains_header_and_totals(self):
        report = portfolio_report.build_report([
            {"task_id": "X-1", "title": "T", "summary": "s", "planner_status": "done",
             "repo": "R", "initiative": "my-init", "priority": 1,
             "closeout_md": "Done!", "closed_at": None,
             "runtime_status": "done", "last_runtime_error": None},
        ])
        md = portfolio_report.render_markdown(report)
        self.assertIn("# Portfolio Status Report", md)
        self.assertIn("my-init", md)
        self.assertIn("1/1", md)
        self.assertIn("Done!", md)


class TestRenderText(unittest.TestCase):
    def test_contains_initiative_and_task(self):
        report = portfolio_report.build_report([
            {"task_id": "X-1", "title": "My Task", "summary": "s", "planner_status": "todo",
             "repo": "R", "initiative": "my-init", "priority": 1,
             "closeout_md": None, "closed_at": None,
             "runtime_status": None, "last_runtime_error": None},
        ])
        text = portfolio_report.render_text(report)
        self.assertIn("MY-INIT", text)
        self.assertIn("X-1", text)
        self.assertIn("My Task", text)


class TestCLI(unittest.TestCase):
    """Integration tests using the real DB (read-only)."""

    def test_text_output_exits_zero(self):
        r = _run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PORTFOLIO STATUS REPORT", r.stdout)

    def test_markdown_output(self):
        r = _run("--format", "markdown")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# Portfolio Status Report", r.stdout)

    def test_json_output(self):
        r = _run("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("initiatives", data)
        self.assertIn("totals", data)
        self.assertIn("generated_at", data)

    def test_json_all_initiatives_present(self):
        r = _run("--json")
        data = json.loads(r.stdout)
        names = [i["initiative"] for i in data["initiatives"]]
        # All known initiatives should appear
        for expected in ["dispatcher-infrastructure", "task-tooling", "repo-health"]:
            self.assertIn(expected, names, f"missing initiative: {expected}")

    def test_json_all_repos_present(self):
        r = _run("--json")
        data = json.loads(r.stdout)
        all_repos: set[str] = set()
        for initiative in data["initiatives"]:
            for repo in initiative["repos"]:
                all_repos.add(repo["repo"])
        for expected in ["CENTRAL", "MOTO_HELPER"]:
            self.assertIn(expected, all_repos, f"missing repo: {expected}")

    def test_filter_initiative(self):
        r = _run("--json", "--initiative", "dispatcher-infrastructure")
        data = json.loads(r.stdout)
        self.assertEqual(len(data["initiatives"]), 1)
        self.assertEqual(data["initiatives"][0]["initiative"], "dispatcher-infrastructure")

    def test_filter_repo(self):
        r = _run("--json", "--repo", "CENTRAL")
        data = json.loads(r.stdout)
        for initiative in data["initiatives"]:
            for repo in initiative["repos"]:
                self.assertEqual(repo["repo"], "CENTRAL")

    def test_json_flag_alias(self):
        r1 = _run("--json")
        r2 = _run("--format", "json")
        self.assertEqual(json.loads(r1.stdout), json.loads(r2.stdout))

    def test_missing_db_exits_nonzero(self):
        r = _run("--db-path", "/nonexistent/path/db.db")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error", r.stderr)

    def test_summaries_not_empty_for_done_tasks(self):
        r = _run("--json")
        data = json.loads(r.stdout)
        done_tasks = [
            t
            for i in data["initiatives"]
            for repo in i["repos"]
            for t in repo["tasks"]
            if t["planner_status"] == "done"
        ]
        self.assertGreater(len(done_tasks), 0)
        # At least some done tasks should have non-empty summaries
        non_empty = [t for t in done_tasks if t.get("summary")]
        self.assertGreater(len(non_empty), 0)


if __name__ == "__main__":
    unittest.main()
