from __future__ import annotations

from pathlib import Path

from tools.repo_health.contract import build_report, make_check, make_coverage, make_evidence, make_repo

REPO_ROOT = Path("/home/cobra/CENTRAL")


def emit_report() -> dict[str, object]:
    evidence = [
        make_evidence(
            evidence_id="central-task-cli-doc",
            kind="file",
            source=str(REPO_ROOT / "docs" / "central_task_cli.md"),
            summary="Documents the canonical DB and dispatcher control plane.",
        ),
        make_evidence(
            evidence_id="central-db-bootstrap-doc",
            kind="file",
            source=str(REPO_ROOT / "docs" / "central_task_db_bootstrap.md"),
            summary="Documents bootstrap and dependency setup for CENTRAL DB workflows.",
        ),
        make_evidence(
            evidence_id="central-runtime-script",
            kind="file",
            source=str(REPO_ROOT / "scripts" / "central_runtime.py"),
            summary="Implements the CENTRAL-native dispatcher runtime.",
        ),
        make_evidence(
            evidence_id="central-reconcile-test",
            kind="file",
            source=str(REPO_ROOT / "tests" / "test_central_runtime_reconcile.py"),
            summary="Smoke-tests dispatcher-to-worker reconciliation behavior.",
        ),
        make_evidence(
            evidence_id="central-worker-status-test",
            kind="file",
            source=str(REPO_ROOT / "tests" / "test_central_runtime_worker_status.sh"),
            summary="Validates worker-status inspection and stale-worker heuristics.",
        ),
    ]

    checks = [
        make_check(
            check_id="workspace",
            label="Workspace shape recognized",
            requirement="mandatory",
            status="pass",
            summary="CENTRAL exposes task DB, dispatcher, and generated-surface entrypoints in stable locations.",
            evidence_ids=["central-task-cli-doc", "central-runtime-script"],
        ),
        make_check(
            check_id="dependencies",
            label="Dependency/bootstrap path documented",
            requirement="mandatory",
            status="pass",
            summary="Bootstrap and DB initialization flow are documented for a fresh checkout.",
            evidence_ids=["central-db-bootstrap-doc"],
        ),
        make_check(
            check_id="tests",
            label="Automated validation coverage",
            requirement="mandatory",
            status="pass",
            summary="Runtime reconcile and worker-status smoke coverage are present.",
            evidence_ids=["central-reconcile-test", "central-worker-status-test"],
        ),
        make_check(
            check_id="runtime",
            label="Runtime/service health",
            requirement="mandatory",
            status="pass",
            summary="Dispatcher runtime is a first-class surface in this repo.",
            evidence_ids=["central-runtime-script", "central-task-cli-doc"],
        ),
        make_check(
            check_id="build",
            label="Build or packaging path",
            requirement="optional",
            status="not_applicable",
            summary="CENTRAL is operated as a Python script/tooling repo rather than a packaged build artifact.",
            notes="Automation repos can mark build not_applicable when no distinct packaging surface exists.",
        ),
    ]

    return build_report(
        repo=make_repo(
            repo_id="CENTRAL",
            display_name="CENTRAL",
            repo_root=REPO_ROOT,
            adapter_name="central.health",
            adapter_version="0.1.0",
            profile="automation",
        ),
        checks=checks,
        coverage=make_coverage(
            status="coverage_unknown",
            summary="CENTRAL does not expose a measured coverage percentage in this operator-facing rollout.",
            notes="Use coverage_unknown until a real coverage tool emits a percentage.",
        ),
        evidence=evidence,
        headline="CENTRAL example adapter shows working status plus explicit coverage_unknown coverage semantics",
    )
