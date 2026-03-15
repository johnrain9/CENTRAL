from __future__ import annotations

from pathlib import Path

from tools.repo_health.contract import build_report, make_check, make_coverage, make_evidence, make_repo

REPO_ROOT = Path("/home/cobra/motoHelper")


def emit_report() -> dict[str, object]:
    evidence = [
        make_evidence(
            evidence_id="moto-readme",
            kind="file",
            source=str(REPO_ROOT / "README.md"),
            summary="README documents stack, setup, and current validation commands.",
        ),
        make_evidence(
            evidence_id="moto-package-json",
            kind="file",
            source=str(REPO_ROOT / "package.json"),
            summary="Package scripts declare dev, build, start, and lint entrypoints.",
        ),
        make_evidence(
            evidence_id="moto-lockfile",
            kind="file",
            source=str(REPO_ROOT / "pnpm-lock.yaml"),
            summary="Dependency lockfile exists for repeatable installs.",
        ),
        make_evidence(
            evidence_id="moto-page",
            kind="file",
            source=str(REPO_ROOT / "src" / "app" / "page.tsx"),
            summary="Next.js application entrypoint is present.",
        ),
    ]

    checks = [
        make_check(
            check_id="workspace",
            label="Workspace shape recognized",
            requirement="mandatory",
            status="pass",
            summary="The repo presents a standard Next.js application layout.",
            evidence_ids=["moto-package-json", "moto-page"],
        ),
        make_check(
            check_id="dependencies",
            label="Dependency/bootstrap path documented",
            requirement="mandatory",
            status="pass",
            summary="README setup and the pnpm lockfile establish the dependency contract.",
            evidence_ids=["moto-readme", "moto-lockfile"],
        ),
        make_check(
            check_id="tests",
            label="Automated validation coverage",
            requirement="mandatory",
            status="unknown",
            summary="README validation currently covers lint and build only; no dedicated automated test suite is claimed yet.",
            notes="Use unknown, not not_applicable, when the repo should eventually have a stronger validation story but does not today.",
        ),
        make_check(
            check_id="build",
            label="Build or packaging path",
            requirement="optional",
            status="pass",
            summary="The app has explicit build and start commands.",
            evidence_ids=["moto-package-json", "moto-readme"],
        ),
        make_check(
            check_id="runtime",
            label="Runtime/service health",
            requirement="optional",
            status="unknown",
            summary="This example does not assert a canonical deployed runtime probe yet.",
            notes="Leave runtime unknown until the adapter has concrete runtime evidence such as a smoke command or health endpoint.",
        ),
    ]

    return build_report(
        repo=make_repo(
            repo_id="MOTOHELPER",
            display_name="motoHelper",
            repo_root=REPO_ROOT,
            adapter_name="motohelper.health",
            adapter_version="0.1.0",
            profile="application",
        ),
        checks=checks,
        coverage=make_coverage(
            status="coverage_unknown",
            summary="motoHelper does not currently expose a measured coverage percentage.",
            notes="Coverage remains explicit until a tool emits a real percentage.",
        ),
        evidence=evidence,
        headline="motoHelper example adapter shows how to keep tests and coverage explicit when they are still unknown",
    )
