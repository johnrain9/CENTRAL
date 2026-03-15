from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "repo-health.v2"
BUNDLE_SCHEMA_VERSION = "repo-health-bundle.v1"
CHECK_STATUSES = ("pass", "warn", "fail", "unknown", "not_applicable")
SUMMARY_STATUSES = ("pass", "warn", "fail", "unknown")
CHECK_REQUIREMENTS = ("mandatory", "optional")
EVIDENCE_KINDS = ("file", "command", "service", "note")
PROFILES = ("application", "automation", "service_only", "library")
COVERAGE_STATUSES = ("measured", "coverage_unknown", "not_applicable")

PROFILE_CHECKS: dict[str, tuple[dict[str, str], ...]] = {
    "application": (
        {"check_id": "workspace", "label": "Workspace shape recognized", "requirement": "mandatory"},
        {"check_id": "dependencies", "label": "Dependency/bootstrap path documented", "requirement": "mandatory"},
        {"check_id": "tests", "label": "Automated validation coverage", "requirement": "mandatory"},
        {"check_id": "build", "label": "Build or packaging path", "requirement": "optional"},
        {"check_id": "runtime", "label": "Runtime/service health", "requirement": "optional"},
    ),
    "automation": (
        {"check_id": "workspace", "label": "Workspace shape recognized", "requirement": "mandatory"},
        {"check_id": "dependencies", "label": "Dependency/bootstrap path documented", "requirement": "mandatory"},
        {"check_id": "tests", "label": "Automated validation coverage", "requirement": "mandatory"},
        {"check_id": "runtime", "label": "Runtime/service health", "requirement": "mandatory"},
        {"check_id": "build", "label": "Build or packaging path", "requirement": "optional"},
    ),
    "service_only": (
        {"check_id": "workspace", "label": "Workspace shape recognized", "requirement": "mandatory"},
        {"check_id": "dependencies", "label": "Dependency/bootstrap path documented", "requirement": "mandatory"},
        {"check_id": "runtime", "label": "Runtime/service health", "requirement": "mandatory"},
        {"check_id": "tests", "label": "Automated validation coverage", "requirement": "optional"},
        {"check_id": "build", "label": "Build or packaging path", "requirement": "optional"},
    ),
    "library": (
        {"check_id": "workspace", "label": "Workspace shape recognized", "requirement": "mandatory"},
        {"check_id": "dependencies", "label": "Dependency/bootstrap path documented", "requirement": "mandatory"},
        {"check_id": "tests", "label": "Automated validation coverage", "requirement": "mandatory"},
        {"check_id": "build", "label": "Build or packaging path", "requirement": "optional"},
        {"check_id": "runtime", "label": "Runtime/service health", "requirement": "optional"},
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_repo(
    *,
    repo_id: str,
    display_name: str,
    repo_root: str | Path,
    adapter_name: str,
    adapter_version: str,
    profile: str,
) -> dict[str, Any]:
    return {
        "repo_id": repo_id,
        "display_name": display_name,
        "repo_root": str(repo_root),
        "adapter_name": adapter_name,
        "adapter_version": adapter_version,
        "profile": profile,
    }


def make_evidence(
    *,
    evidence_id: str,
    kind: str,
    source: str,
    summary: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "source": source,
        "summary": summary,
        "observed_at": observed_at or utc_now(),
    }


def make_check(
    *,
    check_id: str,
    label: str,
    requirement: str,
    status: str,
    summary: str,
    evidence_ids: list[str] | None = None,
    command: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "check_id": check_id,
        "label": label,
        "requirement": requirement,
        "status": status,
        "summary": summary,
        "evidence_ids": list(evidence_ids or []),
    }
    if command:
        payload["command"] = command
    if notes:
        payload["notes"] = notes
    return payload


def make_coverage(
    *,
    status: str,
    summary: str,
    measured_percent: float | None = None,
    evidence_ids: list[str] | None = None,
    command: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "evidence_ids": list(evidence_ids or []),
    }
    if measured_percent is not None:
        payload["measured_percent"] = round(float(measured_percent), 2)
    if command:
        payload["command"] = command
    if notes:
        payload["notes"] = notes
    return payload


def default_checks_for_profile(profile: str) -> tuple[dict[str, str], ...]:
    if profile not in PROFILE_CHECKS:
        raise ValueError(f"unsupported repo-health profile: {profile}")
    return PROFILE_CHECKS[profile]


def derive_working_status(checks: list[dict[str, Any]]) -> str:
    mandatory = [check for check in checks if check.get("requirement") == "mandatory"]
    if any(check.get("status") == "fail" for check in mandatory):
        return "fail"
    if any(check.get("status") == "unknown" for check in mandatory):
        return "unknown"
    if any(check.get("status") == "warn" for check in mandatory):
        return "warn"
    if any(check.get("status") == "fail" for check in checks):
        return "warn"
    if any(check.get("status") in {"warn", "unknown"} for check in checks):
        return "warn"
    return "pass"


def derive_evidence_quality(checks: list[dict[str, Any]], coverage: dict[str, Any]) -> str:
    mandatory = [check for check in checks if check.get("requirement") == "mandatory"]
    if any(check.get("status") == "unknown" for check in mandatory):
        return "unknown"
    if coverage.get("status") == "coverage_unknown":
        return "warn"
    if any(check.get("status") == "unknown" for check in checks):
        return "warn"
    return "pass"


def derive_rollup_status(statuses: list[str]) -> str:
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if any(status == "warn" for status in statuses):
        return "warn"
    return "pass"


def status_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in CHECK_STATUSES}
    for check in checks:
        counts[str(check.get("status") or "unknown")] += 1
    return counts


def build_report(
    *,
    repo: dict[str, Any],
    checks: list[dict[str, Any]],
    coverage: dict[str, Any],
    evidence: list[dict[str, Any]],
    headline: str | None = None,
    generated_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    working_status = derive_working_status(checks)
    evidence_quality = derive_evidence_quality(checks, coverage)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or utc_now(),
        "repo": repo,
        "summary": {
            "working_status": working_status,
            "evidence_quality": evidence_quality,
            "overall_status": derive_rollup_status([working_status, evidence_quality]),
            "headline": headline or f"{repo['display_name']} working={working_status} evidence={evidence_quality}",
            "counts": status_counts(checks),
        },
        "checks": checks,
        "coverage": coverage,
        "evidence": evidence,
    }
    if metadata:
        report["metadata"] = metadata
    errors = validate_report(report)
    if errors:
        message = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"invalid repo-health report\n{message}")
    return report


def build_bundle(
    reports: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary_statuses = [report["summary"]["overall_status"] for report in reports]
    working_statuses = [report["summary"]["working_status"] for report in reports]
    evidence_statuses = [report["summary"]["evidence_quality"] for report in reports]
    payload: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now(),
        "summary": {
            "repo_count": len(reports),
            "working_status": derive_rollup_status(working_statuses),
            "evidence_quality": derive_rollup_status(evidence_statuses),
            "overall_status": derive_rollup_status(summary_statuses),
        },
        "repos": reports,
    }
    if metadata:
        payload["metadata"] = metadata
    errors = validate_bundle(payload)
    if errors:
        message = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"invalid repo-health bundle\n{message}")
    return payload


def stub_report(
    *,
    repo_id: str,
    display_name: str,
    repo_root: str | Path,
    profile: str,
    adapter_name: str = "repo_health.stub",
    adapter_version: str = "0.1.0",
) -> dict[str, Any]:
    checks = [
        make_check(
            check_id=spec["check_id"],
            label=spec["label"],
            requirement=spec["requirement"],
            status="unknown",
            summary=f"{spec['label']} has not been onboarded yet.",
            notes="Keep the canonical check id and replace unknown with pass, warn, fail, or not_applicable once evidence exists.",
        )
        for spec in default_checks_for_profile(profile)
    ]
    return build_report(
        repo=make_repo(
            repo_id=repo_id,
            display_name=display_name,
            repo_root=repo_root,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            profile=profile,
        ),
        checks=checks,
        coverage=make_coverage(
            status="coverage_unknown",
            summary="Coverage has not been onboarded yet.",
            notes="Use measured coverage when a tool emits a real percentage; otherwise keep coverage_unknown.",
        ),
        evidence=[],
        headline=f"{display_name} stub adapter created from the canonical repo-health contract",
    )


def validate_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top_level = ("schema_version", "generated_at", "repo", "summary", "checks", "coverage", "evidence")
    for key in required_top_level:
        if key not in report:
            errors.append(f"missing top-level field: {key}")
    if errors:
        return errors

    if report["schema_version"] != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {report['schema_version']}")

    repo = report["repo"]
    if not isinstance(repo, dict):
        errors.append("repo must be an object")
        return errors
    for key in ("repo_id", "display_name", "repo_root", "adapter_name", "adapter_version", "profile"):
        if not repo.get(key):
            errors.append(f"repo.{key} is required")
    profile = repo.get("profile")
    if profile not in PROFILES:
        errors.append(f"repo.profile must be one of {PROFILES}")

    summary = report["summary"]
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
        return errors
    for key in ("working_status", "evidence_quality", "overall_status"):
        if summary.get(key) not in SUMMARY_STATUSES:
            errors.append(f"summary.{key} must be one of {SUMMARY_STATUSES}")
    if not summary.get("headline"):
        errors.append("summary.headline is required")

    checks = report["checks"]
    coverage = report["coverage"]
    evidence = report["evidence"]
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
        return errors
    if not isinstance(coverage, dict):
        errors.append("coverage must be an object")
        return errors
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
        return errors

    evidence_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            errors.append("every evidence entry must be an object")
            continue
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            errors.append("every evidence entry needs evidence_id")
            continue
        if evidence_id in evidence_ids:
            errors.append(f"duplicate evidence_id: {evidence_id}")
        evidence_ids.add(evidence_id)
        if item.get("kind") not in EVIDENCE_KINDS:
            errors.append(f"evidence {evidence_id} kind must be one of {EVIDENCE_KINDS}")
        if not item.get("source"):
            errors.append(f"evidence {evidence_id} source is required")
        if not item.get("summary"):
            errors.append(f"evidence {evidence_id} summary is required")
        if not item.get("observed_at"):
            errors.append(f"evidence {evidence_id} observed_at is required")

    check_ids: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            errors.append("every check entry must be an object")
            continue
        check_id = item.get("check_id")
        if not check_id:
            errors.append("every check entry needs check_id")
            continue
        if check_id in check_ids:
            errors.append(f"duplicate check_id: {check_id}")
        check_ids.add(check_id)
        if item.get("requirement") not in CHECK_REQUIREMENTS:
            errors.append(f"check {check_id} requirement must be one of {CHECK_REQUIREMENTS}")
        if item.get("status") not in CHECK_STATUSES:
            errors.append(f"check {check_id} status must be one of {CHECK_STATUSES}")
        if not item.get("label"):
            errors.append(f"check {check_id} label is required")
        if not item.get("summary"):
            errors.append(f"check {check_id} summary is required")
        if not isinstance(item.get("evidence_ids"), list):
            errors.append(f"check {check_id} evidence_ids must be a list")
            continue
        if item["status"] in {"pass", "warn", "fail"} and not item["evidence_ids"]:
            errors.append(f"check {check_id} with status {item['status']} requires evidence_ids")
        for evidence_id in item["evidence_ids"]:
            if evidence_id not in evidence_ids:
                errors.append(f"check {check_id} references unknown evidence_id: {evidence_id}")

    if coverage.get("status") not in COVERAGE_STATUSES:
        errors.append(f"coverage.status must be one of {COVERAGE_STATUSES}")
    if not coverage.get("summary"):
        errors.append("coverage.summary is required")
    if not isinstance(coverage.get("evidence_ids"), list):
        errors.append("coverage.evidence_ids must be a list")
    else:
        if coverage["status"] == "measured" and not coverage["evidence_ids"]:
            errors.append("coverage.measured requires evidence_ids")
        for evidence_id in coverage["evidence_ids"]:
            if evidence_id not in evidence_ids:
                errors.append(f"coverage references unknown evidence_id: {evidence_id}")
    measured_percent = coverage.get("measured_percent")
    if coverage.get("status") == "measured":
        if not isinstance(measured_percent, (int, float)):
            errors.append("coverage.measured_percent is required for measured coverage")
    elif measured_percent is not None:
        errors.append("coverage.measured_percent is only valid when coverage.status == measured")

    if profile in PROFILE_CHECKS:
        expected_ids = {item["check_id"] for item in PROFILE_CHECKS[profile]}
        missing = sorted(expected_ids - check_ids)
        if missing:
            errors.append(f"repo profile {profile} is missing canonical checks: {', '.join(missing)}")

    working_status = derive_working_status(checks)
    evidence_quality = derive_evidence_quality(checks, coverage)
    overall_status = derive_rollup_status([working_status, evidence_quality])
    if summary.get("working_status") != working_status:
        errors.append(
            f"summary.working_status={summary.get('working_status')} does not match derived status {working_status}"
        )
    if summary.get("evidence_quality") != evidence_quality:
        errors.append(
            f"summary.evidence_quality={summary.get('evidence_quality')} does not match derived status {evidence_quality}"
        )
    if summary.get("overall_status") != overall_status:
        errors.append(
            f"summary.overall_status={summary.get('overall_status')} does not match derived status {overall_status}"
        )
    return errors


def validate_bundle(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top_level = ("schema_version", "generated_at", "summary", "repos")
    for key in required_top_level:
        if key not in bundle:
            errors.append(f"missing top-level field: {key}")
    if errors:
        return errors

    if bundle["schema_version"] != BUNDLE_SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {bundle['schema_version']}")

    summary = bundle.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
        return errors
    for key in ("repo_count", "working_status", "evidence_quality", "overall_status"):
        if key not in summary:
            errors.append(f"summary.{key} is required")
    for key in ("working_status", "evidence_quality", "overall_status"):
        if summary.get(key) not in SUMMARY_STATUSES:
            errors.append(f"summary.{key} must be one of {SUMMARY_STATUSES}")

    repos = bundle.get("repos")
    if not isinstance(repos, list):
        errors.append("repos must be a list")
        return errors
    if summary.get("repo_count") != len(repos):
        errors.append(f"summary.repo_count={summary.get('repo_count')} does not match len(repos)={len(repos)}")

    working_statuses: list[str] = []
    evidence_statuses: list[str] = []
    overall_statuses: list[str] = []
    for index, report in enumerate(repos):
        if not isinstance(report, dict):
            errors.append(f"repo report at index {index} must be an object")
            continue
        errors.extend(f"repos[{index}]: {error}" for error in validate_report(report))
        report_summary = report.get("summary") or {}
        working_statuses.append(report_summary.get("working_status"))
        evidence_statuses.append(report_summary.get("evidence_quality"))
        overall_statuses.append(report_summary.get("overall_status"))

    if not errors:
        working_status = derive_rollup_status(working_statuses)
        evidence_quality = derive_rollup_status(evidence_statuses)
        overall_status = derive_rollup_status(overall_statuses)
        if summary.get("working_status") != working_status:
            errors.append(
                f"summary.working_status={summary.get('working_status')} does not match derived status {working_status}"
            )
        if summary.get("evidence_quality") != evidence_quality:
            errors.append(
                f"summary.evidence_quality={summary.get('evidence_quality')} does not match derived status {evidence_quality}"
            )
        if summary.get("overall_status") != overall_status:
            errors.append(
                f"summary.overall_status={summary.get('overall_status')} does not match derived status {overall_status}"
            )
    return errors
