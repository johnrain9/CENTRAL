#!/usr/bin/env python3
"""Tests for the canonical repo-health aggregation command."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import repo_health
from tools.repo_health.contract import validate_bundle


class RepoHealthTests(unittest.TestCase):
    def test_coverage_from_repo_root_requires_real_percentage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repo_health_cov_") as tmpdir:
            root = Path(tmpdir)
            (root / "coverage.xml").write_text('<coverage line-rate="0.845"/>', encoding="utf-8")
            coverage, evidence = repo_health.coverage_from_repo_root(root)
        self.assertEqual(coverage["status"], "measured")
        self.assertAlmostEqual(coverage["measured_percent"], 84.5)
        self.assertEqual(len(evidence), 1)

    def test_render_report_includes_working_evidence_and_coverage(self) -> None:
        bundle = {
            "generated_at": "2026-03-15T00:00:00Z",
            "summary": {
                "working_status": "fail",
                "evidence_quality": "warn",
                "overall_status": "fail",
            },
            "repos": [
                {
                    "repo": {"display_name": "dispatcher"},
                    "summary": {
                        "working_status": "pass",
                        "evidence_quality": "warn",
                        "headline": "dispatcher healthy",
                    },
                    "coverage": {"status": "coverage_unknown", "summary": "coverage missing"},
                    "checks": [{"check_id": "runtime", "status": "pass", "summary": "running"}],
                },
                {
                    "repo": {"display_name": "aimSoloAnalysis"},
                    "summary": {
                        "working_status": "fail",
                        "evidence_quality": "warn",
                        "headline": "tests failing",
                    },
                    "coverage": {"status": "coverage_unknown", "summary": "coverage missing"},
                    "checks": [{"check_id": "tests", "status": "fail", "summary": "pytest failed"}],
                },
            ],
        }
        report = repo_health.render_report(bundle)
        self.assertIn("working=fail evidence=warn overall=fail", report)
        self.assertIn("coverage_unknown", report)
        self.assertIn("aimSoloAnalysis", report)

    def test_snapshot_cli_renders_partial_health_when_smoke_and_coverage_are_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repo_health_adapter_") as tmpdir:
            root = Path(tmpdir)
            aim_adapter = root / "aim_adapter.py"
            moto_adapter = root / "moto_adapter.py"
            aim_adapter.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'schema_version': 1,",
                        "  'generated_at': '2026-03-15T00:00:00+00:00',",
                        "  'overall_status': 'error',",
                        "  'headline': 'build failed; tests failed; coverage unknown',",
                        "  'checks': [",
                        "    {'name': 'build', 'category': 'build', 'status': 'error', 'summary': 'npm run build failed.', 'command': ['npm', 'run', 'build']},",
                        "    {'name': 'tests', 'category': 'tests', 'status': 'error', 'summary': 'pytest suite failed.', 'command': ['python', '-m', 'pytest', '-q']},",
                        "    {'name': 'coverage', 'category': 'coverage', 'status': 'unknown', 'summary': 'Coverage missing.'}",
                        "  ]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )
            moto_adapter.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'schema_version': 1,",
                        "  'generated_at': '2026-03-15T00:00:00+00:00',",
                        "  'overall_status': 'warn',",
                        "  'headline': 'build unavailable; tests unknown; coverage unknown',",
                        "  'checks': [",
                        "    {'name': 'build', 'category': 'build', 'status': 'unavailable', 'summary': 'Dependencies missing.', 'command': ['pnpm', 'build']},",
                        "    {'name': 'tests', 'category': 'tests', 'status': 'unknown', 'summary': 'No test suite yet.'},",
                        "    {'name': 'coverage', 'category': 'coverage', 'status': 'unknown', 'summary': 'Coverage missing.'}",
                        "  ]",
                        "}))",
                    ]
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["CENTRAL_REPO_HEALTH_AIM_ADAPTER"] = str(aim_adapter)
            env["CENTRAL_REPO_HEALTH_MOTO_ADAPTER"] = str(moto_adapter)
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "repo_health.py"),
                    "snapshot",
                    "--repo",
                    "aimSoloAnalysis",
                    "--repo",
                    "motoHelper",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(validate_bundle(payload), [])
        self.assertEqual(payload["summary"]["repo_count"], 2)
        reports = {report["repo"]["display_name"]: report for report in payload["repos"]}
        self.assertEqual(reports["aimSoloAnalysis"]["summary"]["working_status"], "fail")
        self.assertEqual(reports["aimSoloAnalysis"]["coverage"]["status"], "coverage_unknown")
        self.assertEqual(reports["motoHelper"]["summary"]["working_status"], "unknown")
        smoke_check = next(check for check in reports["motoHelper"]["checks"] if check["check_id"] == "smoke")
        self.assertEqual(smoke_check["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
