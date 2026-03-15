from __future__ import annotations

from pathlib import Path

from tools.repo_health.contract import build_report, make_check, make_coverage, make_evidence, make_repo, stub_report

REPO_ROOT = Path("/path/to/repo")


def emit_report() -> dict[str, object]:
    """
    Copy this file into a repo-local health location, then replace the stub
    checks with real evidence-backed checks as onboarding advances.
    """

    report = stub_report(
        repo_id="NEW_REPO",
        display_name="New Repo",
        repo_root=REPO_ROOT,
        profile="application",
        adapter_name="new_repo.health",
        adapter_version="0.1.0",
    )

    evidence = [
        make_evidence(
            evidence_id="repo-readme",
            kind="file",
            source=str(REPO_ROOT / "README.md"),
            summary="Primary setup or operator documentation for the repo.",
        )
    ]

    checks = [
        make_check(
            check_id="workspace",
            label="Workspace shape recognized",
            requirement="mandatory",
            status="pass",
            summary="The adapter can locate the repo entrypoints and primary docs.",
            evidence_ids=["repo-readme"],
        ),
        make_check(
            check_id="dependencies",
            label="Dependency/bootstrap path documented",
            requirement="mandatory",
            status="unknown",
            summary="Dependency installation has not been audited yet.",
            notes="Keep unknown until there is concrete install or bootstrap evidence.",
        ),
        make_check(
            check_id="tests",
            label="Automated validation coverage",
            requirement="mandatory",
            status="unknown",
            summary="Test or release-gate coverage has not been mapped yet.",
            notes="If the repo truly has no automated tests yet, keep this unknown and explain the gap.",
        ),
        make_check(
            check_id="build",
            label="Build or packaging path",
            requirement="optional",
            status="not_applicable",
            summary="Not every repo has a build artifact.",
            notes="Use not_applicable only when the repo truly has no build/package surface.",
        ),
        make_check(
            check_id="runtime",
            label="Runtime/service health",
            requirement="optional",
            status="unknown",
            summary="Runtime behavior has not been mapped yet.",
            notes="For service-only repos, make runtime a mandatory evidence-backed check by choosing profile=service_only.",
        ),
    ]

    return build_report(
        repo=make_repo(
            repo_id="NEW_REPO",
            display_name="New Repo",
            repo_root=REPO_ROOT,
            adapter_name="new_repo.health",
            adapter_version="0.1.0",
            profile="application",
        ),
        checks=checks,
        coverage=make_coverage(
            status="coverage_unknown",
            summary="Coverage has not been measured yet.",
            notes="Use measured coverage only when a tool emits a real percentage.",
        ),
        evidence=evidence,
        headline="New Repo starter adapter using the canonical repo-health contract",
    )
