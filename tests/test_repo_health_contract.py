from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.repo_health.contract import build_bundle, validate_bundle, validate_report
from tools.repo_health.examples import aimsoloanalysis_adapter, central_adapter, motohelper_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]


class RepoHealthContractTest(unittest.TestCase):
    def test_example_adapters_validate_for_initial_repo_set(self) -> None:
        for module in (central_adapter, aimsoloanalysis_adapter, motohelper_adapter):
            with self.subTest(adapter=module.__name__):
                report = module.emit_report()
                self.assertEqual(validate_report(report), [])
                self.assertIn(report["coverage"]["status"], {"measured", "coverage_unknown", "not_applicable"})
                self.assertIn(report["summary"]["working_status"], {"pass", "warn", "fail", "unknown"})
                self.assertIn(report["summary"]["evidence_quality"], {"pass", "warn", "fail", "unknown"})

    def test_bundle_validator_accepts_initial_repo_examples(self) -> None:
        bundle = build_bundle(
            [
                central_adapter.emit_report(),
                aimsoloanalysis_adapter.emit_report(),
                motohelper_adapter.emit_report(),
            ]
        )
        self.assertEqual(validate_bundle(bundle), [])
        self.assertEqual(bundle["summary"]["repo_count"], 3)

    def test_new_repo_can_be_stubbed_via_documented_cli_flow(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.repo_health.cli",
                "stub",
                "--repo-id",
                "NEW_REPO",
                "--display-name",
                "New Repo",
                "--repo-root",
                "/tmp/new_repo",
                "--profile",
                "application",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(validate_report(report), [])
        self.assertEqual(report["coverage"]["status"], "coverage_unknown")
        checks = {item["check_id"]: item for item in report["checks"]}
        self.assertEqual(checks["tests"]["status"], "unknown")
        self.assertEqual(checks["build"]["requirement"], "optional")

    def test_validate_cli_accepts_example_adapter(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.repo_health.cli",
                "validate",
                str(REPO_ROOT / "tools" / "repo_health" / "examples" / "central_adapter.py"),
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["repo_id"], "CENTRAL")
        self.assertEqual(payload["working_status"], "pass")


if __name__ == "__main__":
    unittest.main()
