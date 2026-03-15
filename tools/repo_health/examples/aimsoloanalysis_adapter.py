from __future__ import annotations

from pathlib import Path

from tools.repo_health.contract import build_report, make_check, make_coverage, make_evidence, make_repo

REPO_ROOT = Path("/home/cobra/aimSoloAnalysis")


def emit_report() -> dict[str, object]:
    evidence = [
        make_evidence(
            evidence_id="aim-package-json",
            kind="file",
            source=str(REPO_ROOT / "package.json"),
            summary="Root scripts expose the UI build and dev entrypoints.",
        ),
        make_evidence(
            evidence_id="aim-ui-package-json",
            kind="file",
            source=str(REPO_ROOT / "ui-v2" / "package.json"),
            summary="UI v2 has explicit build and dev scripts.",
        ),
        make_evidence(
            evidence_id="aim-api-app",
            kind="file",
            source=str(REPO_ROOT / "api" / "app.py"),
            summary="FastAPI service entrypoint for local analytics APIs.",
        ),
        make_evidence(
            evidence_id="aim-release-gate-doc",
            kind="file",
            source=str(REPO_ROOT / "docs" / "release_gate_workflow.md"),
            summary="Documents the end-to-end release gate and validation workflow.",
        ),
        make_evidence(
            evidence_id="aim-tests-dir",
            kind="file",
            source=str(REPO_ROOT / "tests" / "test_release_gate_workflow.py"),
            summary="Repository includes automated tests for release-gate behavior.",
        ),
        make_evidence(
            evidence_id="aim-ui-build-script",
            kind="file",
            source=str(REPO_ROOT / "ui-v2" / "scripts" / "build.mjs"),
            summary="Concrete UI build script for the browser surface.",
        ),
        make_evidence(
            evidence_id="aim-runtime-doc",
            kind="file",
            source=str(REPO_ROOT / "docs" / "wsl2_native_js_ui_design.md"),
            summary="Documents how to start the API and frontend locally.",
        ),
    ]

    checks = [
        make_check(
            check_id="workspace",
            label="Workspace shape recognized",
            requirement="mandatory",
            status="pass",
            summary="The repo has explicit API, analytics, and UI entrypoints.",
            evidence_ids=["aim-package-json", "aim-api-app"],
        ),
        make_check(
            check_id="dependencies",
            label="Dependency/bootstrap path documented",
            requirement="mandatory",
            status="pass",
            summary="Root and UI packages declare the build/dev bootstrap surfaces.",
            evidence_ids=["aim-package-json", "aim-ui-package-json"],
        ),
        make_check(
            check_id="tests",
            label="Automated validation coverage",
            requirement="mandatory",
            status="pass",
            summary="Release-gate validation and automated test coverage are documented and present.",
            evidence_ids=["aim-release-gate-doc", "aim-tests-dir"],
        ),
        make_check(
            check_id="build",
            label="Build or packaging path",
            requirement="optional",
            status="pass",
            summary="The UI build path is explicit and committed.",
            evidence_ids=["aim-package-json", "aim-ui-build-script"],
        ),
        make_check(
            check_id="runtime",
            label="Runtime/service health",
            requirement="optional",
            status="warn",
            summary="Local service startup is documented, but this example does not claim a deployed runtime health probe yet.",
            evidence_ids=["aim-api-app", "aim-runtime-doc"],
        ),
    ]

    return build_report(
        repo=make_repo(
            repo_id="AIMSOLOANALYSIS",
            display_name="aimSoloAnalysis",
            repo_root=REPO_ROOT,
            adapter_name="aimsoloanalysis.health",
            adapter_version="0.1.0",
            profile="application",
        ),
        checks=checks,
        coverage=make_coverage(
            status="coverage_unknown",
            summary="No measured coverage percentage is exposed by the current aimSoloAnalysis rollout.",
            notes="Coverage should stay explicit: measured percentage or coverage_unknown.",
        ),
        evidence=evidence,
        headline="aimSoloAnalysis example adapter mixes release-gate, build, and explicit coverage_unknown evidence",
    )
