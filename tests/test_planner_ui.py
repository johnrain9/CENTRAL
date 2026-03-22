#!/usr/bin/env python3
"""
Focused validation test for the CENTRAL Planner Status UI.

Tests:
1. /api/data returns all required sections with correct types
2. Summary fields are consistent with dispatcher/worker state
3. Active workers contain required operational fields
4. Actionable section splits impl vs audit tasks
5. Needs attention section merges review, failures, blocked
6. /api/task/<id> returns task detail with events
7. HTML endpoint serves a non-empty response

Run from CENTRAL root:
    python3 tests/test_planner_ui.py
"""

import json
import importlib.util
import os
import subprocess
import sys
import unittest
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_SCRIPT = os.path.join(REPO_ROOT, "scripts", "planner_ui.py")
DB_SCRIPT = os.path.join(REPO_ROOT, "scripts", "central_task_db.py")

_CLIENT = None


def _db(*args) -> Any:
    result = subprocess.run(
        [sys.executable, DB_SCRIPT] + list(args),
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=15,
    )
    return json.loads(result.stdout)


def _get(path: str) -> Any:
    if _CLIENT is None:
        raise RuntimeError("planner ui test client not initialized")
    response = _CLIENT.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} failed with status {response.status_code}")
    return response.get_json()


def _get_html(path: str) -> bytes:
    if _CLIENT is None:
        raise RuntimeError("planner ui test client not initialized")
    response = _CLIENT.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} failed with status {response.status_code}")
    return response.data


class TestPlannerUIServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _CLIENT
        spec = importlib.util.spec_from_file_location("planner_ui", UI_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load planner_ui module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CLIENT = module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        global _CLIENT
        _CLIENT = None

    # ── /api/data structure ──────────────────────────────────────────────────

    def test_api_data_has_all_sections(self):
        data = _get("/api/data")
        required_keys = {
            "generated_at", "errors", "dispatcher", "workers",
            "actionable", "needs_attention", "awaiting_audit",
            "by_repo", "recent_changes", "summary",
        }
        self.assertEqual(required_keys, set(data.keys()) & required_keys,
                         "Missing required top-level sections")

    def test_api_data_no_errors(self):
        data = _get("/api/data")
        self.assertEqual([], data["errors"], f"API reported errors: {data['errors']}")

    def test_summary_fields_present(self):
        data = _get("/api/data")
        s = data["summary"]
        required = [
            "dispatcher_running", "max_workers", "active_workers", "idle_slots",
            "eligible_count", "awaiting_audit_count", "failed_audit_count",
            "blocked_count", "stale_count",
        ]
        for field in required:
            self.assertIn(field, s, f"summary missing field: {field}")

    def test_summary_counts_are_nonnegative(self):
        data = _get("/api/data")
        s = data["summary"]
        for field in ["eligible_count", "awaiting_audit_count", "failed_audit_count",
                      "blocked_count", "stale_count", "active_workers"]:
            self.assertGreaterEqual(s[field], 0, f"summary.{field} should be >= 0")

    def test_summary_active_workers_matches_workers_section(self):
        data = _get("/api/data")
        active_from_summary = data["summary"]["active_workers"]
        active_from_workers = len(data["workers"]["active"])
        self.assertEqual(active_from_summary, active_from_workers,
                         "summary.active_workers should equal len(workers.active)")

    # ── Dispatcher section ───────────────────────────────────────────────────

    def test_dispatcher_section_has_required_fields(self):
        data = _get("/api/data")
        d = data["dispatcher"]
        for field in ["running", "worker_mode", "default_model", "max_workers", "claim_policy"]:
            self.assertIn(field, d, f"dispatcher missing field: {field}")

    # ── Workers section ──────────────────────────────────────────────────────

    def test_workers_section_structure(self):
        data = _get("/api/data")
        w = data["workers"]
        self.assertIn("active", w)
        self.assertIn("recent", w)
        self.assertIsInstance(w["active"], list)
        self.assertIsInstance(w["recent"], list)

    def test_active_workers_have_operational_fields(self):
        data = _get("/api/data")
        for worker in data["workers"]["active"]:
            self.assertIn("task_id", worker, "worker missing task_id")
            self.assertIn("title", worker, "worker missing title")
            self.assertIn("heartbeat", worker, "worker missing heartbeat")
            self.assertIn("log", worker, "worker missing log")
            self.assertIn("worker", worker, "worker missing worker info")
            hb = worker["heartbeat"]
            self.assertIn("age_seconds", hb, "heartbeat missing age_seconds")
            log = worker["log"]
            self.assertIn("size_bytes", log, "log missing size_bytes")
            self.assertIn("signal", log, "log missing signal")

    def test_active_workers_have_model_field(self):
        data = _get("/api/data")
        for worker in data["workers"]["active"]:
            w_info = worker.get("worker", {})
            self.assertIn("model", w_info, f"worker {worker['task_id']} missing model")

    # ── Actionable section ───────────────────────────────────────────────────

    def test_actionable_has_impl_and_audit(self):
        data = _get("/api/data")
        a = data["actionable"]
        self.assertIn("implementation", a)
        self.assertIn("audit", a)
        self.assertIsInstance(a["implementation"], list)
        self.assertIsInstance(a["audit"], list)

    def test_actionable_impl_not_audit_type(self):
        data = _get("/api/data")
        for t in data["actionable"]["implementation"]:
            self.assertNotEqual(t.get("task_type"), "audit",
                                f"impl list contains audit task: {t.get('task_id')}")

    def test_actionable_audit_tasks_are_audit_type(self):
        data = _get("/api/data")
        for t in data["actionable"]["audit"]:
            self.assertEqual(t.get("task_type"), "audit",
                             f"audit list contains non-audit task: {t.get('task_id')}")

    # ── Needs Attention section ──────────────────────────────────────────────

    def test_attention_items_have_task_id(self):
        data = _get("/api/data")
        for item in data["needs_attention"]:
            self.assertIn("task_id", item, "attention item missing task_id")

    def test_attention_items_have_source(self):
        data = _get("/api/data")
        valid_sources = {"review", "failure", "blocked"}
        for item in data["needs_attention"]:
            src = item.get("_source", "")
            self.assertIn(src, valid_sources, f"attention item has unknown source: {src!r}")

    def test_attention_no_duplicates(self):
        data = _get("/api/data")
        ids = [item["task_id"] for item in data["needs_attention"]]
        self.assertEqual(len(ids), len(set(ids)), "attention list contains duplicate task_ids")

    # ── Awaiting Audit section ───────────────────────────────────────────────

    def test_awaiting_audit_fields(self):
        data = _get("/api/data")
        for item in data["awaiting_audit"]:
            self.assertIn("task_id", item)
            self.assertIn("repo", item)
            self.assertIn("title", item)

    # ── By Repo section ──────────────────────────────────────────────────────

    def test_by_repo_has_required_fields(self):
        data = _get("/api/data")
        for repo in data["by_repo"]:
            for field in ["repo", "total", "running", "eligible", "blocked"]:
                self.assertIn(field, repo, f"repo row missing field: {field}")

    def test_by_repo_counts_nonnegative(self):
        data = _get("/api/data")
        for repo in data["by_repo"]:
            for field in ["total", "running", "eligible", "blocked"]:
                self.assertGreaterEqual(repo[field], 0)

    def test_by_repo_initiatives_have_progress_fields(self):
        data = _get("/api/data")
        for repo in data["by_repo"]:
            for item in repo.get("initiatives", []):
                for field in ["initiative", "total", "done", "in_progress"]:
                    self.assertIn(field, item, f"initiative row missing field: {field}")

    # ── Recent Changes ───────────────────────────────────────────────────────

    def test_recent_changes_have_task_id(self):
        data = _get("/api/data")
        for change in data["recent_changes"]:
            self.assertIn("task_id", change)
            self.assertIn("repo", change)
            self.assertIn("title", change)

    # ── Task detail endpoint ─────────────────────────────────────────────────

    def test_task_detail_endpoint(self):
        # Find a task ID from the live DB
        panel = _db("view-planner-panel", "--json")
        changes = panel.get("changed_since", [])
        if not changes:
            self.skipTest("No recent tasks to inspect")
        task_id = changes[0]["task_id"]

        detail = _get(f"/api/task/{task_id}")
        self.assertEqual(detail.get("task_id"), task_id)
        self.assertIn("title", detail)
        self.assertIn("events", detail)
        self.assertIsInstance(detail["events"], list)

    def test_task_detail_has_audit_block(self):
        panel = _db("view-planner-panel", "--json")
        changes = panel.get("changed_since", [])
        if not changes:
            self.skipTest("No recent tasks")
        task_id = changes[0]["task_id"]
        detail = _get(f"/api/task/{task_id}")
        self.assertIn("audit", detail)

    # ── HTML endpoint ────────────────────────────────────────────────────────

    def test_html_endpoint_returns_html(self):
        html = _get_html("/")
        self.assertGreater(len(html), 1000, "HTML response is suspiciously short")
        self.assertIn(b"CENTRAL", html)
        self.assertIn(b"Active Workers", html)
        self.assertIn(b"Actionable Now", html)
        self.assertIn(b"Awaiting Audit", html)
        self.assertIn(b"Needs Attention", html)
        self.assertIn(b"By Repo", html)
        self.assertIn(b"Recent Changes", html)

    def test_html_has_auto_refresh_logic(self):
        html = _get_html("/")
        self.assertIn(b"fetchData", html)
        self.assertIn(b"scheduleRefresh", html)

    def test_html_has_dark_theme(self):
        html = _get_html("/")
        # Verify dark-theme CSS variables and near-black background are present
        self.assertIn(b"--bg:", html)
        self.assertIn(b"0f1117", html)  # near-black background color

    def test_html_has_repo_card_sort_and_toggle_controls(self):
        html = _get_html("/")
        self.assertIn(b"id=\"repo-sort\"", html)
        self.assertIn(b"id=\"repo-hide-completed\"", html)
        self.assertIn(b"handleByRepoControlsChange", html)

    def test_html_has_repo_progress_sort_modes(self):
        html = _get_html("/")
        self.assertIn(b"progress_desc", html)
        self.assertIn(b"progress_asc", html)
        self.assertIn(b"active_desc", html)
        self.assertIn(b"active_asc", html)

    def test_html_has_cmd_k_shortcut(self):
        html = _get_html("/")
        self.assertIn(b"metaKey", html, "cmd+k handler must check metaKey")
        self.assertIn(b"ctrlKey", html, "ctrl+k handler must check ctrlKey")
        self.assertIn(b"f-search", html, "cmd+k handler must target #f-search")
        self.assertIn(b"kbd-highlight", html, "cmd+k handler must apply kbd-highlight class")

    def test_html_kbd_highlight_css(self):
        html = _get_html("/")
        self.assertIn(b"kbd-highlight", html, "kbd-highlight CSS class must be defined")

    # ── Consistency with CLI ─────────────────────────────────────────────────

    def test_eligible_count_matches_cli(self):
        cli_eligible = _db("view-eligible", "--json")
        data = _get("/api/data")
        api_eligible = (len(data["actionable"]["implementation"]) +
                        len(data["actionable"]["audit"]))
        cli_count = len(cli_eligible) if isinstance(cli_eligible, list) else 0
        self.assertEqual(api_eligible, cli_count,
                         f"API eligible count ({api_eligible}) doesn't match CLI ({cli_count})")

    def test_awaiting_audit_count_matches_summary(self):
        data = _get("/api/data")
        summary_count = data["summary"]["awaiting_audit_count"]
        section_count = len(data["awaiting_audit"])
        self.assertEqual(summary_count, section_count,
                         f"summary.awaiting_audit_count ({summary_count}) != len(awaiting_audit) ({section_count})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
