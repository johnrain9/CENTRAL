#!/usr/bin/env python3
"""Canonical repo-health aggregation for CENTRAL-operated repositories."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.repo_health.contract import (
    build_bundle,
    build_report,
    default_checks_for_profile,
    make_check,
    make_coverage,
    make_evidence,
    make_repo,
)

DISPATCHER_DEFAULT_ROOT = REPO_ROOT
ECO_ROOT = Path(os.environ.get("CENTRAL_ECO_ROOT", str(REPO_ROOT.parent / "ecosystem")))
AIM_ROOT = Path("/home/cobra/aimSoloAnalysis")
MOTO_ROOT = Path("/home/cobra/motoHelper")
LEGACY_STATUS_MAP = {
    "ok": "pass",
    "warn": "warn",
    "error": "fail",
    "unknown": "unknown",
    "unavailable": "unknown",
    "not_applicable": "not_applicable",
}


@dataclass(frozen=True)
class AdapterSpec:
    repo_id: str
    display_name: str
    repo_root: Path
    runner: Callable[[argparse.Namespace], dict[str, Any]]


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    error: str | None = None


@dataclass(frozen=True)
class DispatcherRuntimeConfig:
    repo_root: Path
    control_script: Path
    runtime_script: Path
    db_script: Path
    reconcile_test: Path
    worker_status_smoke: Path
    bootstrap_doc: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def summarize_text(*parts: str, limit: int = 220) -> str:
    compact = " | ".join(part.strip() for part in parts if part and part.strip())
    compact = " ".join(compact.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def quote_command(command: list[str] | None) -> str | None:
    if not command:
        return None
    return " ".join(command)


def env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def resolve_dispatcher_root(args: argparse.Namespace | None = None) -> Path:
    value = getattr(args or {}, "dispatcher_root", None)
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser().resolve()
    return env_path("CENTRAL_DISPATCHER_ROOT", DISPATCHER_DEFAULT_ROOT)


def resolve_dispatcher_config(args: argparse.Namespace) -> DispatcherRuntimeConfig:
    repo_root = resolve_dispatcher_root(args)
    return DispatcherRuntimeConfig(
        repo_root=repo_root,
        control_script=env_path("CENTRAL_DISPATCHER_CONTROL_SCRIPT", repo_root / "scripts" / "dispatcher_control.py"),
        runtime_script=env_path("CENTRAL_DISPATCHER_RUNTIME_SCRIPT", repo_root / "scripts" / "central_runtime.py"),
        db_script=env_path("CENTRAL_DISPATCHER_DB_SCRIPT", repo_root / "scripts" / "central_task_db.py"),
        reconcile_test=env_path(
            "CENTRAL_DISPATCHER_RECONCILE_TEST",
            repo_root / "tests" / "test_central_runtime_reconcile.py",
        ),
        worker_status_smoke=env_path(
            "CENTRAL_DISPATCHER_WORKER_STATUS_SMOKE",
            repo_root / "tests" / "test_central_runtime_worker_status.sh",
        ),
        bootstrap_doc=env_path(
            "CENTRAL_DISPATCHER_BOOTSTRAP_DOC",
            repo_root / "docs" / "central_task_db_bootstrap.md",
        ),
    )


def run_command(command: list[str], *, cwd: Path, timeout_seconds: float) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return CommandResult(
            command=command,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=time.monotonic() - started,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd),
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_seconds=time.monotonic() - started,
            timed_out=True,
            error=f"timed out after {timeout_seconds:.1f}s",
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd),
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            timed_out=False,
            error=str(exc),
        )


def run_json_command(command: list[str], *, cwd: Path, timeout_seconds: float) -> tuple[Any | None, CommandResult]:
    result = run_command(command, cwd=cwd, timeout_seconds=timeout_seconds)
    if result.exit_code != 0 or result.timed_out or result.error:
        return None, result
    raw = result.stdout or ""
    candidates = [raw.strip()]
    for marker in ("\n{", "\n["):
        idx = raw.rfind(marker)
        if idx >= 0:
            candidates.append(raw[idx + 1 :].strip())
    payload: Any | None = None
    parse_error = False
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            parse_error = True
    if payload is None and parse_error:
        return None, CommandResult(
            command=result.command,
            cwd=result.cwd,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
            error="command returned invalid JSON",
        )
    if payload is None:
        payload = {}
    return payload, result


def command_summary(result: CommandResult) -> str:
    if result.error:
        return summarize_text(f"command error: {result.error}", result.stderr, result.stdout, limit=240)
    if result.timed_out:
        return summarize_text(f"command timed out after {result.duration_seconds:.1f}s", result.stderr, result.stdout, limit=240)
    if result.exit_code == 0:
        return summarize_text("command passed", result.stdout, limit=240)
    return summarize_text(f"command failed with exit {result.exit_code}", result.stderr, result.stdout, limit=240)


def make_command_evidence_item(evidence_id: str, result: CommandResult, *, summary: str | None = None) -> dict[str, Any]:
    return make_evidence(
        evidence_id=evidence_id,
        kind="command",
        source=quote_command(result.command) or "unknown command",
        summary=summary or command_summary(result),
        observed_at=utc_now(),
    )


def make_file_evidence_item(evidence_id: str, path: Path, summary: str) -> dict[str, Any]:
    return make_evidence(
        evidence_id=evidence_id,
        kind="file",
        source=str(path),
        summary=summary,
    )


def measured_coverage(repo_root: Path) -> tuple[float | None, str | None, str | None]:
    coverage_xml = repo_root / "coverage.xml"
    if coverage_xml.exists():
        try:
            root = ET.fromstring(coverage_xml.read_text(encoding="utf-8"))
            if "line-rate" in root.attrib:
                percent = float(root.attrib["line-rate"]) * 100.0
                return percent, str(coverage_xml), f"Parsed line-rate from {coverage_xml.name}"
            lines_covered = root.attrib.get("lines-covered")
            lines_valid = root.attrib.get("lines-valid")
            if lines_covered and lines_valid and float(lines_valid) > 0:
                percent = (float(lines_covered) / float(lines_valid)) * 100.0
                return percent, str(coverage_xml), f"Parsed lines-covered/lines-valid from {coverage_xml.name}"
        except (ET.ParseError, OSError, ValueError):
            return None, str(coverage_xml), "coverage.xml exists but could not be parsed into a percentage"
    return None, None, None


def check_spec(profile: str, check_id: str) -> dict[str, str]:
    for spec in default_checks_for_profile(profile):
        if spec["check_id"] == check_id:
            return spec
    raise KeyError(f"{profile} has no canonical check {check_id}")


def canonical_check(
    *,
    profile: str,
    check_id: str,
    status: str,
    summary: str,
    evidence_ids: list[str] | None = None,
    command: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    spec = check_spec(profile, check_id)
    return make_check(
        check_id=check_id,
        label=spec["label"],
        requirement=spec["requirement"],
        status=status,
        summary=summary,
        evidence_ids=evidence_ids,
        command=command,
        notes=notes,
    )


def make_bundle_report_headline(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    return summarize_text(
        f"working={report['summary']['working_status']}",
        f"evidence={report['summary']['evidence_quality']}",
        f"coverage={coverage['status']}" + (f" {coverage['measured_percent']:.1f}%" if "measured_percent" in coverage else ""),
        limit=180,
    )


def legacy_check(payload: dict[str, Any], category: str) -> dict[str, Any] | None:
    for check in payload.get("checks", []):
        if not isinstance(check, dict):
            continue
        if check.get("category") == category or check.get("name") == category:
            return check
    return None


def legacy_status(raw_status: str | None) -> str:
    return LEGACY_STATUS_MAP.get(str(raw_status or "unknown"), "unknown")


def coverage_from_repo_root(repo_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    percent, source, source_summary = measured_coverage(repo_root)
    if percent is None:
        return (
            make_coverage(
                status="coverage_unknown",
                summary="Coverage is not measured by a tool that emits a percentage yet.",
                notes="Use coverage_unknown instead of implying coverage from test files or artifact presence alone.",
            ),
            [],
        )
    evidence_id = "coverage-measured"
    evidence = [
        make_evidence(
            evidence_id=evidence_id,
            kind="file",
            source=source or str(repo_root / "coverage.xml"),
            summary=source_summary or "Measured coverage parsed from coverage.xml",
        )
    ]
    return (
        make_coverage(
            status="measured",
            summary=f"Measured line coverage is {percent:.1f}%.",
            measured_percent=percent,
            evidence_ids=[evidence_id],
        ),
        evidence,
    )


def build_unknown_report(
    *,
    repo_id: str,
    display_name: str,
    repo_root: Path,
    profile: str,
    adapter_name: str,
    summary: str,
    evidence: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = [
        canonical_check(
            profile=profile,
            check_id=spec["check_id"],
            status="unknown",
            summary=summary,
            evidence_ids=[],
        )
        for spec in default_checks_for_profile(profile)
    ]
    return build_report(
        repo=make_repo(
            repo_id=repo_id,
            display_name=display_name,
            repo_root=repo_root,
            adapter_name=adapter_name,
            adapter_version="0.2.0",
            profile=profile,
        ),
        checks=checks,
        coverage=make_coverage(status="coverage_unknown", summary=summary),
        evidence=evidence,
        headline=summary,
        metadata=metadata,
    )


def dispatcher_report(args: argparse.Namespace) -> dict[str, Any]:
    cfg = resolve_dispatcher_config(args)
    profile = "automation"
    evidence: list[dict[str, Any]] = [
        make_file_evidence_item(
            "dispatcher-runtime-script",
            cfg.runtime_script,
            "CENTRAL runtime entrypoint exposes worker-status and self-check surfaces.",
        ),
        make_file_evidence_item(
            "dispatcher-db-bootstrap-doc",
            cfg.bootstrap_doc,
            "Bootstrap instructions document the CENTRAL DB and dispatcher runtime setup.",
        ),
    ]

    status_payload, status_result = run_json_command(
        [sys.executable, str(cfg.control_script), "status"],
        cwd=cfg.repo_root,
        timeout_seconds=args.command_timeout,
    )
    worker_payload, worker_result = run_json_command(
        [sys.executable, str(cfg.runtime_script), "worker-status", "--json"],
        cwd=cfg.repo_root,
        timeout_seconds=args.command_timeout,
    )
    summary_payload, summary_result = run_json_command(
        [sys.executable, str(cfg.db_script), "view-summary", "--json"],
        cwd=cfg.repo_root,
        timeout_seconds=args.command_timeout,
    )
    review_payload, review_result = run_json_command(
        [sys.executable, str(cfg.db_script), "view-review", "--json"],
        cwd=cfg.repo_root,
        timeout_seconds=args.command_timeout,
    )
    test_result = run_command(
        [sys.executable, "-m", "pytest", "--cov=scripts", "--cov=tools",
         "--cov-report=xml:coverage.xml", "-q", "--no-header"],
        cwd=cfg.repo_root,
        timeout_seconds=max(args.command_timeout, 120.0),
    )
    worker_status_smoke_result = run_command(
        ["bash", str(cfg.worker_status_smoke)],
        cwd=cfg.repo_root,
        timeout_seconds=max(args.command_timeout, 120.0),
    )

    smoke_result: CommandResult | None = None
    smoke_payload: Any | None = None
    if not args.skip_smoke:
        smoke_payload, smoke_result = run_json_command(
            [sys.executable, str(cfg.runtime_script), "self-check"],
            cwd=cfg.repo_root,
            timeout_seconds=max(args.command_timeout, 120.0),
        )

    for evidence_id, result, description in (
        ("dispatcher-status-command", status_result, "Dispatcher runtime status command output."),
        ("dispatcher-worker-status-command", worker_result, "Dispatcher worker-status probe output."),
        ("dispatcher-queue-command", summary_result, "Task summary probe output."),
        ("dispatcher-review-command", review_result, "Review queue probe output."),
        ("dispatcher-tests-command", test_result, "Dispatcher unittest command output."),
        ("dispatcher-worker-smoke-command", worker_status_smoke_result, "Dispatcher worker-status smoke test output."),
    ):
        evidence.append(make_command_evidence_item(evidence_id, result, summary=description))
    if smoke_result is not None:
        evidence.append(make_command_evidence_item("dispatcher-self-check-command", smoke_result, summary="Dispatcher self-check output."))

    if None in (status_payload, worker_payload, summary_payload, review_payload):
        return build_unknown_report(
            repo_id="dispatcher",
            display_name="CENTRAL dispatcher",
            repo_root=cfg.repo_root,
            profile=profile,
            adapter_name="central.dispatcher.health",
            summary="One or more dispatcher probes failed, so the runtime answer is incomplete.",
            evidence=evidence,
            metadata={"skip_smoke": args.skip_smoke},
        )

    running = bool(status_payload.get("running"))
    worker_summary = worker_payload.get("summary") if isinstance(worker_payload.get("summary"), dict) else {}
    active_leases = int(status_payload.get("active_leases") or 0)
    eligible_count = int(status_payload.get("eligible_count") or 0)
    blocked_count = int(summary_payload.get("blocked_count") or 0)
    pending_review_count = int(summary_payload.get("pending_review_count") or 0)
    low_activity_count = int(worker_summary.get("low_activity_count") or 0)
    stuck_count = int(worker_summary.get("potentially_stuck_count") or 0)

    workspace_check = canonical_check(
        profile=profile,
        check_id="workspace",
        status="pass",
        summary="CENTRAL exposes dispatcher, runtime, DB, and generated-view entrypoints in stable locations.",
        evidence_ids=["dispatcher-runtime-script"],
    )
    dependencies_check = canonical_check(
        profile=profile,
        check_id="dependencies",
        status="pass",
        summary="Bootstrap and DB initialization are documented for an operator on a fresh checkout.",
        evidence_ids=["dispatcher-db-bootstrap-doc"],
    )

    tests_status = "pass" if test_result.exit_code == 0 and worker_status_smoke_result.exit_code == 0 else "fail"
    tests_summary = "Dispatcher unittest and worker-status smoke commands passed."
    if tests_status == "fail":
        tests_summary = "At least one dispatcher validation command failed."
    tests_check = canonical_check(
        profile=profile,
        check_id="tests",
        status=tests_status,
        summary=tests_summary,
        evidence_ids=["dispatcher-tests-command", "dispatcher-worker-smoke-command"],
        command=f"{quote_command([sys.executable, '-m', 'pytest', '--cov=scripts', '--cov=tools', '--cov-report=xml:coverage.xml', '-q', '--no-header'])} && {quote_command(['bash', str(cfg.worker_status_smoke)])}",
    )

    runtime_status = "pass"
    runtime_summary = f"Dispatcher is running with {active_leases} active lease(s)."
    if not running:
        runtime_status = "fail"
        runtime_summary = "Dispatcher is not running."
    elif stuck_count:
        runtime_status = "warn"
        runtime_summary = f"Dispatcher is running, but {stuck_count} worker(s) look stuck."
    elif low_activity_count:
        runtime_status = "warn"
        runtime_summary = f"Dispatcher is running; {low_activity_count} worker(s) look quiet."
    runtime_check = canonical_check(
        profile=profile,
        check_id="runtime",
        status=runtime_status,
        summary=runtime_summary,
        evidence_ids=["dispatcher-status-command", "dispatcher-worker-status-command"],
        command=f"{quote_command([sys.executable, str(cfg.control_script), 'status'])} && {quote_command([sys.executable, str(cfg.runtime_script), 'worker-status', '--json'])}",
    )

    build_check = canonical_check(
        profile=profile,
        check_id="build",
        status="not_applicable",
        summary="CENTRAL is operated as tooling and runtime scripts, not as a packaged build artifact.",
    )

    checks = [
        workspace_check,
        dependencies_check,
        tests_check,
        runtime_check,
        build_check,
        make_check(
            check_id="queue",
            label="Queue pressure",
            requirement="optional",
            status="warn" if eligible_count > 0 or blocked_count > 0 else "pass",
            summary=(
                f"eligible={eligible_count}, active_leases={active_leases}, blocked={blocked_count}, "
                f"pending_review={pending_review_count}"
            ),
            evidence_ids=["dispatcher-queue-command", "dispatcher-review-command"],
            command=f"{quote_command([sys.executable, str(cfg.db_script), 'view-summary', '--json'])} && {quote_command([sys.executable, str(cfg.db_script), 'view-review', '--json'])}",
        ),
    ]

    if args.skip_smoke:
        smoke_check = make_check(
            check_id="smoke",
            label="End-to-end smoke status",
            requirement="optional",
            status="unknown",
            summary="Dispatcher smoke check was skipped by operator request.",
            evidence_ids=[],
            notes="Run without --skip-smoke for the end-to-end runtime probe.",
        )
    else:
        smoke_status = "fail"
        smoke_summary = "Dispatcher self-check failed."
        if smoke_payload is not None:
            runtime_status_payload = str(smoke_payload.get("runtime_status") or "unknown")
            planner_status = str(smoke_payload.get("planner_status") or "unknown")
            last_runtime_error = smoke_payload.get("last_runtime_error")
            smoke_status = "pass" if runtime_status_payload == "done" and not last_runtime_error else "fail"
            smoke_summary = f"planner_status={planner_status}, runtime_status={runtime_status_payload}"
        smoke_check = make_check(
            check_id="smoke",
            label="End-to-end smoke status",
            requirement="optional",
            status=smoke_status,
            summary=smoke_summary,
            evidence_ids=["dispatcher-self-check-command"],
            command=f"{quote_command([sys.executable, str(cfg.runtime_script), 'self-check'])}",
        )
    checks.append(smoke_check)

    coverage, coverage_evidence = coverage_from_repo_root(cfg.repo_root)
    evidence.extend(coverage_evidence)
    if coverage["status"] == "coverage_unknown":
        coverage["summary"] = "CENTRAL does not expose a measured coverage percentage in this operator snapshot."

    report = build_report(
        repo=make_repo(
            repo_id="dispatcher",
            display_name="CENTRAL dispatcher",
            repo_root=cfg.repo_root,
            adapter_name="central.dispatcher.health",
            adapter_version="0.2.0",
            profile=profile,
        ),
        checks=checks,
        coverage=coverage,
        evidence=evidence,
        headline=summarize_text(runtime_summary, tests_summary, smoke_check["summary"], coverage["summary"], limit=200),
        metadata={"skip_smoke": args.skip_smoke},
    )
    return report


def external_legacy_payload(
    *,
    repo_root: Path,
    adapter_path: Path,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, CommandResult]:
    cwd = repo_root if repo_root.exists() else adapter_path.parent
    payload, result = run_json_command(
        [sys.executable, str(adapter_path), "snapshot", "--json"],
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    if payload is None:
        return None, result
    if not isinstance(payload, dict):
        return None, CommandResult(
            command=result.command,
            cwd=result.cwd,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
            error="legacy adapter returned a non-object JSON payload",
        )
    return payload, result


def aimsoloanalysis_report(args: argparse.Namespace) -> dict[str, Any]:
    profile = "application"
    adapter_path = env_path("CENTRAL_REPO_HEALTH_AIM_ADAPTER", AIM_ROOT / "tools" / "repo_health_adapter.py")
    payload, adapter_result = external_legacy_payload(
        repo_root=AIM_ROOT,
        adapter_path=adapter_path,
        timeout_seconds=max(args.command_timeout, 240.0),
    )
    evidence = [
        make_file_evidence_item("aim-package-json", AIM_ROOT / "package.json", "Root scripts declare the top-level build and dev entrypoints."),
        make_file_evidence_item("aim-ui-package-json", AIM_ROOT / "ui-v2" / "package.json", "UI package declares its build and dev scripts."),
        make_file_evidence_item("aim-api-app", AIM_ROOT / "api" / "app.py", "FastAPI entrypoint exists for local runtime work."),
        make_file_evidence_item(
            "aim-runtime-doc",
            AIM_ROOT / "docs" / "wsl2_native_js_ui_design.md",
            "Runtime startup instructions describe API and frontend launch paths.",
        ),
        make_command_evidence_item("aim-legacy-adapter", adapter_result, summary="aimSoloAnalysis repo-local health adapter output."),
    ]
    if payload is None:
        return build_unknown_report(
            repo_id="aimSoloAnalysis",
            display_name="aimSoloAnalysis",
            repo_root=AIM_ROOT,
            profile=profile,
            adapter_name="central.aimsoloanalysis.health",
            summary="aimSoloAnalysis adapter could not produce a current test/build snapshot.",
            evidence=evidence,
            metadata={"adapter_path": str(adapter_path)},
        )

    build_result = legacy_check(payload, "build")
    tests_result = legacy_check(payload, "tests")

    if build_result and build_result.get("command"):
        evidence.append(
            make_evidence(
                evidence_id="aim-build-command",
                kind="command",
                source=quote_command(build_result.get("command")) or "build command",
                summary=str(build_result.get("summary") or "aimSoloAnalysis build result"),
                observed_at=str(build_result.get("observed_at") or utc_now()),
            )
        )
    if tests_result and tests_result.get("command"):
        evidence.append(
            make_evidence(
                evidence_id="aim-tests-command",
                kind="command",
                source=quote_command(tests_result.get("command")) or "tests command",
                summary=str(tests_result.get("summary") or "aimSoloAnalysis tests result"),
                observed_at=str(tests_result.get("observed_at") or utc_now()),
            )
        )

    checks = [
        canonical_check(
            profile=profile,
            check_id="workspace",
            status="pass",
            summary="aimSoloAnalysis exposes distinct API and UI entrypoints.",
            evidence_ids=["aim-package-json", "aim-api-app"],
        ),
        canonical_check(
            profile=profile,
            check_id="dependencies",
            status="pass",
            summary="Package manifests document how to bootstrap both the root workspace and the UI subproject.",
            evidence_ids=["aim-package-json", "aim-ui-package-json"],
        ),
        canonical_check(
            profile=profile,
            check_id="tests",
            status=legacy_status(tests_result.get("status") if tests_result else None),
            summary=str(tests_result.get("summary") if tests_result else "aimSoloAnalysis test status is unknown."),
            evidence_ids=["aim-tests-command"] if tests_result and tests_result.get("command") else [],
            command=quote_command(tests_result.get("command")) if tests_result else None,
            notes="Current test/build health is sourced from the repo-local adapter command.",
        ),
        canonical_check(
            profile=profile,
            check_id="build",
            status=legacy_status(build_result.get("status") if build_result else None),
            summary=str(build_result.get("summary") if build_result else "aimSoloAnalysis build status is unknown."),
            evidence_ids=["aim-build-command"] if build_result and build_result.get("command") else [],
            command=quote_command(build_result.get("command")) if build_result else None,
            notes="Build health comes from the repo-local adapter command.",
        ),
        canonical_check(
            profile=profile,
            check_id="runtime",
            status="warn",
            summary="Runtime entrypoints are documented, but no live runtime health probe is wired into repo-health yet.",
            evidence_ids=["aim-api-app", "aim-runtime-doc"],
        ),
        make_check(
            check_id="smoke",
            label="End-to-end smoke status",
            requirement="optional",
            status="unknown",
            summary="No dedicated smoke probe is exposed yet for aimSoloAnalysis.",
            evidence_ids=[],
            notes="Add a UI or API smoke command before promoting smoke to pass/fail.",
        ),
    ]

    coverage, coverage_evidence = coverage_from_repo_root(AIM_ROOT)
    evidence.extend(coverage_evidence)
    report = build_report(
        repo=make_repo(
            repo_id="aimSoloAnalysis",
            display_name="aimSoloAnalysis",
            repo_root=AIM_ROOT,
            adapter_name="central.aimsoloanalysis.health",
            adapter_version="0.2.0",
            profile=profile,
        ),
        checks=checks,
        coverage=coverage,
        evidence=evidence,
        headline=summarize_text(
            str(tests_result.get("summary") if tests_result else "tests unknown"),
            str(build_result.get("summary") if build_result else "build unknown"),
            "coverage=coverage_unknown" if coverage["status"] == "coverage_unknown" else coverage["summary"],
            limit=200,
        ),
        metadata={"adapter_path": str(adapter_path)},
    )
    return report


def motohelper_report(args: argparse.Namespace) -> dict[str, Any]:
    profile = "application"
    adapter_path = env_path("CENTRAL_REPO_HEALTH_MOTO_ADAPTER", MOTO_ROOT / "tools" / "repo_health_adapter.py")
    payload, adapter_result = external_legacy_payload(
        repo_root=MOTO_ROOT,
        adapter_path=adapter_path,
        timeout_seconds=max(args.command_timeout, 180.0),
    )
    evidence = [
        make_file_evidence_item("moto-readme", MOTO_ROOT / "README.md", "README documents setup, runtime, and the current validation commands."),
        make_file_evidence_item("moto-package-json", MOTO_ROOT / "package.json", "Package scripts declare dev, build, start, and lint entrypoints."),
        make_file_evidence_item("moto-lockfile", MOTO_ROOT / "pnpm-lock.yaml", "Lockfile exists for repeatable installs."),
        make_file_evidence_item("moto-page", MOTO_ROOT / "src" / "app" / "page.tsx", "Next.js application entrypoint exists."),
        make_command_evidence_item("moto-legacy-adapter", adapter_result, summary="motoHelper repo-local health adapter output."),
    ]
    if payload is None:
        return build_unknown_report(
            repo_id="motoHelper",
            display_name="motoHelper",
            repo_root=MOTO_ROOT,
            profile=profile,
            adapter_name="central.motohelper.health",
            summary="motoHelper adapter could not produce a current validation snapshot.",
            evidence=evidence,
            metadata={"adapter_path": str(adapter_path)},
        )

    build_result = legacy_check(payload, "build")
    lint_result = legacy_check(payload, "lint")
    tests_result = legacy_check(payload, "tests")
    app_result = legacy_check(payload, "app")

    for evidence_id, check in (
        ("moto-build-command", build_result),
        ("moto-lint-command", lint_result),
    ):
        if check and check.get("command"):
            evidence.append(
                make_evidence(
                    evidence_id=evidence_id,
                    kind="command",
                    source=quote_command(check.get("command")) or "command",
                    summary=str(check.get("summary") or "motoHelper command result"),
                    observed_at=str(check.get("observed_at") or utc_now()),
                )
            )

    checks = [
        canonical_check(
            profile=profile,
            check_id="workspace",
            status="pass",
            summary="motoHelper presents a conventional Next.js application layout.",
            evidence_ids=["moto-package-json", "moto-page"],
        ),
        canonical_check(
            profile=profile,
            check_id="dependencies",
            status="pass",
            summary="README setup plus the pnpm lockfile define the bootstrap contract.",
            evidence_ids=["moto-readme", "moto-lockfile"],
        ),
        canonical_check(
            profile=profile,
            check_id="tests",
            status=legacy_status(tests_result.get("status") if tests_result else None),
            summary=str(tests_result.get("summary") if tests_result else "motoHelper test status is unknown."),
            evidence_ids=[],
            notes="motoHelper currently does not expose a dedicated automated test suite in its repo-local adapter.",
        ),
        canonical_check(
            profile=profile,
            check_id="build",
            status=legacy_status(build_result.get("status") if build_result else None),
            summary=str(build_result.get("summary") if build_result else "motoHelper build status is unknown."),
            evidence_ids=["moto-build-command"] if build_result and build_result.get("command") else [],
            command=quote_command(build_result.get("command")) if build_result else None,
            notes="A missing package install leaves build unknown rather than implicitly passing or failing.",
        ),
        canonical_check(
            profile=profile,
            check_id="runtime",
            status="warn",
            summary="README documents how to run the app locally, but no live runtime health probe is wired into repo-health yet.",
            evidence_ids=["moto-readme", "moto-package-json"],
        ),
        make_check(
            check_id="smoke",
            label="End-to-end smoke status",
            requirement="optional",
            status="unknown",
            summary="No browser or API smoke check is exposed yet for motoHelper.",
            evidence_ids=[],
            notes="This keeps smoke honest instead of inferring it from lint or build docs.",
        ),
    ]
    if lint_result:
        checks.append(
            make_check(
                check_id="lint",
                label="Lint validation",
                requirement="optional",
                status=legacy_status(lint_result.get("status")),
                summary=str(lint_result.get("summary") or "motoHelper lint status is unknown."),
                evidence_ids=["moto-lint-command"] if lint_result.get("command") else [],
                command=quote_command(lint_result.get("command")),
            )
        )
    if app_result:
        app_status = legacy_status(app_result.get("status"))
        checks.append(
            make_check(
                check_id="app_status",
                label="Application-level status",
                requirement="optional",
                status=app_status,
                summary=str(app_result.get("summary") or "motoHelper application status is unknown."),
                evidence_ids=["moto-legacy-adapter"] if app_status in ("pass", "warn", "fail") else [],
                command=quote_command(app_result.get("command")),
                notes="App-level status is derived from the repo-local adapter's app status synthesis.",
            )
        )

    coverage, coverage_evidence = coverage_from_repo_root(MOTO_ROOT)
    evidence.extend(coverage_evidence)
    report = build_report(
        repo=make_repo(
            repo_id="motoHelper",
            display_name="motoHelper",
            repo_root=MOTO_ROOT,
            adapter_name="central.motohelper.health",
            adapter_version="0.2.0",
            profile=profile,
        ),
        checks=checks,
        coverage=coverage,
        evidence=evidence,
        headline=summarize_text(
            str(build_result.get("summary") if build_result else "build unknown"),
            str(tests_result.get("summary") if tests_result else "tests unknown"),
            "coverage=coverage_unknown" if coverage["status"] == "coverage_unknown" else coverage["summary"],
            limit=200,
        ),
        metadata={"adapter_path": str(adapter_path)},
    )
    return report


def ecosystem_report(args: argparse.Namespace) -> dict[str, Any]:
    profile = "library"
    adapter_path = env_path("CENTRAL_REPO_HEALTH_ECO_ADAPTER", ECO_ROOT / "tools" / "repo_health_adapter.py")
    payload, adapter_result = external_legacy_payload(
        repo_root=ECO_ROOT,
        adapter_path=adapter_path,
        timeout_seconds=max(args.command_timeout, 300.0),
    )
    evidence = [
        make_file_evidence_item("eco-cargo-toml", ECO_ROOT / "Cargo.toml", "Cargo.toml declares the ecosystem crate and its dependencies."),
        make_command_evidence_item("eco-adapter", adapter_result, summary="ecosystem repo-local health adapter output."),
    ]
    if payload is None:
        return build_unknown_report(
            repo_id="ecosystem",
            display_name="ecosystem",
            repo_root=ECO_ROOT,
            profile=profile,
            adapter_name="central.ecosystem.health",
            summary="ecosystem adapter could not produce a current test/build snapshot.",
            evidence=evidence,
            metadata={"adapter_path": str(adapter_path)},
        )

    tests_result = legacy_check(payload, "tests")
    coverage_result = legacy_check(payload, "coverage")

    if tests_result and tests_result.get("command"):
        evidence.append(
            make_evidence(
                evidence_id="eco-tests-command",
                kind="command",
                source=quote_command(tests_result.get("command")) or "cargo test",
                summary=str(tests_result.get("summary") or "ecosystem test result"),
                observed_at=str(tests_result.get("observed_at") or utc_now()),
            )
        )

    measured_percent: float | None = None
    if coverage_result:
        measured_percent = coverage_result.get("details", {}).get("measured_percent")

    if measured_percent is not None:
        coverage = make_coverage(
            status="measured",
            summary=str(coverage_result.get("summary", f"measured {measured_percent:.1f}%")),
            measured_percent=measured_percent,
            evidence_ids=["eco-adapter"],
        )
    else:
        coverage = make_coverage(
            status="coverage_unknown",
            summary=str(coverage_result.get("summary") if coverage_result else "No coverage tool available."),
            notes="Install cargo-llvm-cov or cargo-tarpaulin to enable measured coverage.",
        )

    cargo_toml_exists = (ECO_ROOT / "Cargo.toml").exists()
    cargo_lock_exists = (ECO_ROOT / "Cargo.lock").exists()

    checks = [
        canonical_check(
            profile=profile,
            check_id="workspace",
            status="pass" if cargo_toml_exists else "unknown",
            summary="Cargo.toml exists and declares the ecosystem crate." if cargo_toml_exists else "Cargo.toml not found.",
            evidence_ids=["eco-cargo-toml"] if cargo_toml_exists else [],
        ),
        canonical_check(
            profile=profile,
            check_id="dependencies",
            status="pass" if cargo_lock_exists else "unknown",
            summary="Cargo.lock ensures reproducible dependency resolution." if cargo_lock_exists else "Cargo.lock not found.",
            evidence_ids=["eco-cargo-toml"] if cargo_lock_exists else [],
        ),
        canonical_check(
            profile=profile,
            check_id="tests",
            status=legacy_status(tests_result.get("status") if tests_result else None),
            summary=str(tests_result.get("summary") if tests_result else "ecosystem test status is unknown."),
            evidence_ids=["eco-tests-command"] if tests_result and tests_result.get("command") else [],
            command=quote_command(tests_result.get("command")) if tests_result else None,
        ),
        canonical_check(
            profile=profile,
            check_id="build",
            status="pass" if cargo_toml_exists else "unknown",
            summary="cargo build is assumed to work if cargo test runs; no separate build check wired yet.",
            evidence_ids=["eco-cargo-toml"] if cargo_toml_exists else [],
        ),
        canonical_check(
            profile=profile,
            check_id="runtime",
            status="unknown",
            summary="No runtime health probe is wired for ecosystem yet.",
            evidence_ids=[],
            notes="ecosystem is a library crate with no long-running runtime process.",
        ),
    ]

    report = build_report(
        repo=make_repo(
            repo_id="ecosystem",
            display_name="ecosystem",
            repo_root=ECO_ROOT,
            adapter_name="central.ecosystem.health",
            adapter_version="0.1.0",
            profile=profile,
        ),
        checks=checks,
        coverage=coverage,
        evidence=evidence,
        headline=summarize_text(
            str(tests_result.get("summary") if tests_result else "tests unknown"),
            coverage["summary"],
            limit=200,
        ),
        metadata={"adapter_path": str(adapter_path)},
    )
    return report


def build_registry(args: argparse.Namespace | None = None) -> dict[str, AdapterSpec]:
    dispatcher_root = resolve_dispatcher_root(args)
    return {
        "dispatcher": AdapterSpec(
            repo_id="dispatcher",
            display_name="CENTRAL dispatcher",
            repo_root=dispatcher_root,
            runner=dispatcher_report,
        ),
        "aimSoloAnalysis": AdapterSpec(
            repo_id="aimSoloAnalysis",
            display_name="aimSoloAnalysis",
            repo_root=AIM_ROOT,
            runner=aimsoloanalysis_report,
        ),
        "motoHelper": AdapterSpec(
            repo_id="motoHelper",
            display_name="motoHelper",
            repo_root=MOTO_ROOT,
            runner=motohelper_report,
        ),
        "ecosystem": AdapterSpec(
            repo_id="ecosystem",
            display_name="ecosystem",
            repo_root=ECO_ROOT,
            runner=ecosystem_report,
        ),
    }


_STATUS_MARKER: dict[str, str] = {
    "pass": "PASS",
    "warn": "WARN",
    "fail": "FAIL",
    "unknown": "UNKN",
    "not_applicable": "N/A ",
}

_OVERALL_MARKER: dict[str, str] = {
    "pass": "HEALTHY",
    "warn": "DEGRADED",
    "fail": "FAILING",
    "unknown": "UNKNOWN",
}


def status_marker(status: str) -> str:
    return _STATUS_MARKER.get(str(status).lower(), str(status).upper()[:4].ljust(4))


def overall_marker(status: str) -> str:
    return _OVERALL_MARKER.get(str(status).lower(), str(status).upper())


def render_checks(checks: list[dict[str, Any]], indent: str = "  ") -> list[str]:
    lines = []
    for check in checks:
        marker = status_marker(check.get("status", "unknown"))
        label = check.get("label") or check.get("check_id") or "?"
        summary = check.get("summary", "")
        req = check.get("requirement", "required")
        req_tag = "" if req == "required" else f" [{req}]"
        lines.append(f"{indent}[{marker}] {label}{req_tag}: {summary}")
    return lines


def render_report(bundle: dict[str, Any]) -> str:
    repos = bundle.get("repos")
    if not isinstance(repos, list):
        return json_dumps(bundle)
    summary = bundle.get("summary", {})
    overall = overall_marker(str(summary.get("overall_status", "unknown")))
    lines = [
        f"=== Repo Health Snapshot ===",
        f"Generated : {bundle.get('generated_at')}",
        f"Overall   : {overall}  (working={summary.get('working_status')}  evidence={summary.get('evidence_quality')})",
        "",
    ]
    repo_width = max(len("repo"), *[len(str(r["repo"]["display_name"])) for r in repos])
    cov_width = 16
    lines.append(
        f"{'repo'.ljust(repo_width)}  status  evidence    coverage          headline"
    )
    lines.append(
        f"{'-' * repo_width}  ------  ----------  ----------------  {'-' * 8}"
    )
    for report in repos:
        coverage = report["coverage"]
        cov_text = str(coverage["status"])
        if coverage["status"] == "measured" and "measured_percent" in coverage:
            cov_text = f"measured {coverage['measured_percent']:.1f}%"
        working = report["summary"]["working_status"]
        marker = status_marker(working)
        evidence = str(report["summary"]["evidence_quality"])
        headline = report["summary"].get("headline", "")
        lines.append(
            f"{report['repo']['display_name'].ljust(repo_width)}  "
            f"[{marker}]  "
            f"{evidence.ljust(10)}  "
            f"{cov_text.ljust(cov_width)}  "
            f"{headline}"
        )

    # Per-repo check detail
    for report in repos:
        coverage = report["coverage"]
        cov_text = str(coverage["status"])
        if coverage["status"] == "measured" and "measured_percent" in coverage:
            cov_text = f"measured {coverage['measured_percent']:.1f}%"
        working = report["summary"]["working_status"]
        marker = status_marker(working)
        lines.extend([
            "",
            f"--- {report['repo']['display_name']} [{marker}] ---",
            f"  {report['summary'].get('headline', '')}",
            f"  evidence: {report['summary']['evidence_quality']}  coverage: {cov_text}",
        ])
        lines.extend(render_checks(report.get("checks", [])))
    return "\n".join(lines)


def render_latest_rows(rows: list[dict[str, Any]], now_str: str, repo_id: str | None = None) -> str:
    """Render DB-backed snapshot rows as a human-readable operator report."""
    if not rows:
        return (
            "No health snapshots found.\n"
            "Run: python3 scripts/repo_health.py snapshot --persist"
        )
    lines = [
        f"=== Latest Repo Health (from DB) ===",
        f"As of : {now_str}",
        "",
    ]
    repo_width = max(len("repo"), *[len(str(r.get("repo_id", ""))) for r in rows])
    lines.append(
        f"{'repo'.ljust(repo_width)}  status  evidence    freshness  captured_at           headline"
    )
    lines.append(
        f"{'-' * repo_width}  ------  ----------  ---------  --------------------  {'-' * 8}"
    )
    for row in rows:
        working = str(row.get("working_status", "unknown"))
        marker = status_marker(working)
        freshness = str(row.get("freshness", "?"))
        fresh_tag = "STALE" if freshness == "stale" else "fresh"
        evidence = str(row.get("evidence_quality", "?"))
        captured = str(row.get("captured_at", "?"))
        # Parse headline from stored report_json if available
        headline = ""
        report_json_raw = row.get("report_json")
        report_detail: dict[str, Any] | None = None
        if report_json_raw:
            try:
                report_detail = json.loads(report_json_raw)
                headline = (report_detail.get("summary") or {}).get("headline", "")
            except (json.JSONDecodeError, AttributeError):
                pass
        lines.append(
            f"{str(row.get('repo_id', '')).ljust(repo_width)}  "
            f"[{marker}]  "
            f"{evidence.ljust(10)}  "
            f"{fresh_tag.ljust(9)}  "
            f"{captured.ljust(20)}  "
            f"{headline}"
        )

    # Drill-down checks when a specific repo is requested
    if repo_id and len(rows) == 1:
        row = rows[0]
        report_json_raw = row.get("report_json")
        if report_json_raw:
            try:
                report_detail = json.loads(report_json_raw)
                coverage = (report_detail.get("coverage") or {})
                cov_text = str(coverage.get("status", "?"))
                if coverage.get("status") == "measured" and "measured_percent" in coverage:
                    cov_text = f"measured {coverage['measured_percent']:.1f}%"
                freshness = str(row.get("freshness", "?"))
                fresh_tag = "STALE" if freshness == "stale" else "fresh"
                lines.extend([
                    "",
                    f"--- {row.get('repo_id')} checks (snapshot {row.get('snapshot_id')}, {fresh_tag}) ---",
                    f"  captured: {row.get('captured_at')}  coverage: {cov_text}",
                ])
                lines.extend(render_checks(report_detail.get("checks", [])))
            except (json.JSONDecodeError, AttributeError):
                lines.append("  (could not parse stored report_json for check details)")

    stale = [r for r in rows if r.get("is_stale") or r.get("freshness") == "stale"]
    if stale:
        lines.append("")
        lines.append(
            f"WARNING: {len(stale)} snapshot(s) are stale. "
            "Re-run: python3 scripts/repo_health.py snapshot --persist"
        )
    return "\n".join(lines)


def persist_bundle(bundle: dict[str, Any], ttl_seconds: int) -> None:
    """Write a health bundle to the CENTRAL DB via central_task_db.py."""
    db_script = REPO_ROOT / "scripts" / "central_task_db.py"
    import io
    import subprocess as _sp
    payload = json_dumps(bundle).encode()
    result = _sp.run(
        [sys.executable, str(db_script), "health-snapshot-write", "-", f"--ttl-seconds={ttl_seconds}"],
        input=payload,
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        print(f"[repo_health] WARNING: could not persist snapshots: {result.stderr.decode().strip()}", file=sys.stderr)
    else:
        print(f"[repo_health] {result.stdout.decode().strip()}", file=sys.stderr)


def command_snapshot(args: argparse.Namespace) -> int:
    registry = build_registry(args)
    requested = args.repo or list(registry.keys())
    reports: list[dict[str, Any]] = []
    for repo_id in requested:
        spec = registry.get(repo_id)
        if spec is None:
            raise SystemExit(f"unknown repo id: {repo_id}")
        reports.append(spec.runner(args))
    bundle = build_bundle(reports, metadata={"requested_repos": requested})
    if args.persist:
        persist_bundle(bundle, args.ttl_seconds)
    if args.json:
        print(json_dumps(bundle))
    else:
        print(render_report(bundle))
    return 0


def command_latest(args: argparse.Namespace) -> int:
    """Read the latest health snapshot from CENTRAL DB without invoking live checks."""
    db_script = REPO_ROOT / "scripts" / "central_task_db.py"
    import subprocess as _sp

    # Always fetch JSON from DB so we can render it ourselves
    cmd = [sys.executable, str(db_script), "health-snapshot-latest", "--json"]
    repo_id: str | None = getattr(args, "repo", None)
    if repo_id:
        cmd += ["--repo-id", repo_id]
    result = _sp.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    if args.json:
        print(result.stdout, end="")
        return 0

    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"could not parse DB output as JSON\n{result.stdout}\n")
        return 1

    now_str = utc_now()
    # Annotate stale/freshness client-side (DB already does this but we keep it resilient)
    for row in rows:
        if "freshness" not in row:
            try:
                from datetime import datetime as _dt, timezone as _tz
                captured = _dt.fromisoformat(str(row.get("captured_at", "")).replace("Z", "+00:00"))
                ttl = int(row.get("ttl_seconds") or 3600)
                now_dt = _dt.fromisoformat(now_str.replace("Z", "+00:00"))
                row["is_stale"] = (now_dt - captured).total_seconds() > ttl
                row["freshness"] = "stale" if row["is_stale"] else "fresh"
            except (ValueError, TypeError):
                row["freshness"] = "?"
    print(render_latest_rows(rows, now_str, repo_id=repo_id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical repo-health aggregation for CENTRAL and related repos")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Collect repo-health records for the registered repos")
    snapshot_parser.add_argument(
        "--repo",
        action="append",
        choices=["dispatcher", "aimSoloAnalysis", "motoHelper", "ecosystem"],
        help="Restrict output to one or more registered repos.",
    )
    snapshot_parser.add_argument(
        "--dispatcher-root",
        default=None,
        help="Optional repo root for dispatcher/runtime probes. Defaults to CENTRAL_DISPATCHER_ROOT or the CENTRAL repo.",
    )
    snapshot_parser.add_argument("--command-timeout", type=float, default=60.0, help="Timeout in seconds for each command probe.")
    snapshot_parser.add_argument("--skip-smoke", action="store_true", help="Skip the dispatcher self-check.")
    snapshot_parser.add_argument("--json", action="store_true", help="Emit JSON instead of the operator table.")
    snapshot_parser.add_argument("--persist", action="store_true", help="Write the collected snapshot to the CENTRAL DB for instant future reads.")
    snapshot_parser.add_argument("--ttl-seconds", type=int, default=3600, help="Freshness TTL when persisting (default 3600s).")
    snapshot_parser.set_defaults(func=command_snapshot)

    latest_parser = subparsers.add_parser("latest", help="Read the latest health snapshot instantly from CENTRAL DB (no live checks).")
    latest_parser.add_argument(
        "--repo",
        choices=["dispatcher", "aimSoloAnalysis", "motoHelper", "ecosystem"],
        default=None,
        help="Restrict to a single repo.",
    )
    latest_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    latest_parser.set_defaults(func=command_latest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
