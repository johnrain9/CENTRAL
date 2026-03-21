#!/usr/bin/env python3
"""Collect test counts/coverage for a repo and persist a repo-health snapshot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import central_task_db as task_db
from tools.repo_health.contract import build_report, make_check, make_coverage, make_evidence, make_repo

DB_SCRIPT = REPO_ROOT / "scripts" / "central_task_db.py"


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_coverage_xml(repo_root: Path) -> tuple[float | None, str | None, str | None]:
    coverage_xml = repo_root / "coverage.xml"
    if not coverage_xml.exists():
        return None, None, None
    try:
        root = ET.fromstring(coverage_xml.read_text(encoding="utf-8"))
    except (ET.ParseError, OSError):
        return None, str(coverage_xml), "coverage.xml exists but could not be parsed"

    if "line-rate" in root.attrib:
        try:
            return (
                float(root.attrib["line-rate"]) * 100.0,
                str(coverage_xml),
                f"Parsed line-rate from {coverage_xml.name}",
            )
        except (TypeError, ValueError):
            return None, str(coverage_xml), f"coverage.xml exists but line-rate is invalid"

    covered = root.attrib.get("lines-covered")
    valid = root.attrib.get("lines-valid")
    if covered and valid:
        try:
            covered_f = float(covered)
            valid_f = float(valid)
            if valid_f > 0:
                return (
                    (covered_f / valid_f) * 100.0,
                    str(coverage_xml),
                    f"Parsed lines-covered/lines-valid from {coverage_xml.name}",
                )
        except (TypeError, ValueError):
            return None, str(coverage_xml), "coverage.xml exists but values are invalid"
    return None, str(coverage_xml), "coverage.xml does not expose coverage percentage fields"


def resolve_repo_identity(repo_root: Path, db_path: str | None = None) -> dict[str, str]:
    conn, _ = task_db.open_initialized_connection(db_path)
    try:
        resolved = task_db.resolve_repo_reference(conn, str(repo_root), field="repo", allow_missing=True)
    finally:
        conn.close()
    if resolved is None:
        return {
            "repo_id": task_db.normalize_repo_id(str(repo_root)),
            "display_name": repo_root.name,
            "repo_root": str(repo_root),
        }
    return {
        "repo_id": str(resolved["repo_id"]),
        "display_name": str(resolved["display_name"]),
        "repo_root": str(resolved["repo_root"]),
    }


def detect_runner(repo_root: Path) -> tuple[str | None, list[str]]:
    python_manifest = (
        (repo_root / "pyproject.toml").exists()
        or (repo_root / "setup.py").exists()
        or (repo_root / "setup.cfg").exists()
        or (repo_root / "requirements.txt").exists()
        or any(repo_root.glob("*.py"))
    )
    if (repo_root / "scripts" / "tests").is_dir() or python_manifest:
        if importlib.util.find_spec("pytest") is not None:
            command = [sys.executable, "-m", "pytest", "-x"]
            if importlib.util.find_spec("pytest_cov") is not None:
                command.extend(["--cov=.", "--cov-report=xml:coverage.xml"])
            return "python", command
        discovery_root = repo_root / "scripts" / "tests"
        if not discovery_root.is_dir():
            discovery_root = repo_root / "tests"
        if not discovery_root.is_dir() and (repo_root / "scripts" / "tests").is_dir():
            discovery_root = repo_root / "scripts" / "tests"
        return "python", [sys.executable, "-m", "unittest", "discover", "-s", str(discovery_root)]
    if (repo_root / "Cargo.toml").exists():
        return "rust", ["cargo", "test", "--no-fail-fast"]
    if any((repo_root / "tests").glob("*.py")) or any((repo_root / "tests").glob("**/*.py")):
        return "python", [sys.executable, "-m", "unittest", "discover", "-s", str(repo_root / "tests")]
    return None, []


def _extract_count(text: str, key: str) -> int:
    m = re.search(rf"(\d[\d,]*)\s+{re.escape(key)}\b", text, re.IGNORECASE)
    if not m:
        return 0
    return int(m.group(1).replace(",", ""))


def parse_rust_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "ignored": 0, "measured": 0, "filtered out": 0}
    lines = [ln for ln in output.splitlines() if ln.startswith("test result:")]
    for line in lines:
        counts["passed"] += _extract_count(line, "passed")
        counts["failed"] += _extract_count(line, "failed")
        counts["ignored"] += _extract_count(line, "ignored")
        counts["measured"] += _extract_count(line, "measured")
        counts["filtered out"] += _extract_count(line, "filtered out")
    counts["total"] = counts["passed"] + counts["failed"] + counts["ignored"] + counts["measured"] + counts["filtered out"]
    return counts


def parse_pytest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "xpassed": 0, "xfailed": 0, "deselected": 0, "error": 0, "errors": 0}
    # Typical summary line:
    # "= 2 failed, 4 passed, 1 skipped in 0.12s ="
    summary_line = ""
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.startswith("=") and " in " in text and any(tag in text for tag in (" passed", " failed", " skipped", " xpassed", " xfailed")):
            summary_line = text
            break
        if text.endswith(" passed.") and "in" not in text:
            summary_line = text
            break
    if not summary_line:
        return counts
    for token in re.finditer(r"(\d[\d,]*)\s+(passed|failed|skipped|xpassed|xfailed|deselected|errors?|error)", summary_line):
        key = token.group(2)
        if key == "errors":
            key = "error"
        counts[key] += int(token.group(1).replace(",", ""))
    counts["total"] = counts["passed"] + counts["failed"] + counts["skipped"] + counts["xpassed"] + counts["xfailed"]
    return counts


def parse_unittest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "xpassed": 0, "xfailed": 0, "deselected": 0, "error": 0, "errors": 0}
    ran_match = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+", output)
    if not ran_match:
        counts["total"] = 0
        return counts
    total = int(ran_match.group(1))
    failed = 0
    errors = 0
    skipped = 0
    failed_match = re.search(r"FAILED\s+\(([^)]*)\)", output)
    if failed_match:
        for token in failed_match.group(1).split(","):
            token = token.strip()
            match = re.match(r"(failures|errors|skipped)=(\d+)", token)
            if not match:
                continue
            key = match.group(1)
            value = int(match.group(2))
            if key == "failures":
                failed = value
            elif key == "errors":
                errors = value
            elif key == "skipped":
                skipped = value
    elif "OK" in output:
        skipped_match = re.search(r"OK\s+\(skipped=(\d+)\)", output)
        if skipped_match:
            skipped = int(skipped_match.group(1))
    counts["failed"] = failed
    counts["error"] = errors
    counts["errors"] = errors
    counts["skipped"] = skipped
    counts["passed"] = max(0, total - failed - errors - skipped)
    counts["total"] = total
    return counts


def run_tests(runner: str, command: list[str], repo_root: Path, *, timeout_seconds: int) -> tuple[int, str, dict[str, int]]:
    try:
        proc = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, timeout=timeout_seconds)
        exit_code = proc.returncode
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        exit_code = 124
        output = (stdout + "\n" + stderr).strip()
        output += f"\nrepo_health_check timeout after {timeout_seconds}s"
    if runner == "rust":
        counts = parse_rust_counts(output)
    elif command[2:4] == ["unittest", "discover"]:
        counts = parse_unittest_counts(output)
    else:
        counts = parse_pytest_counts(output)
    return exit_code, output, counts


def build_health_report(
    repo_root: Path,
    repo_identity: dict[str, str],
    runner: str,
    exit_code: int,
    command: list[str],
    counts: dict[str, int],
) -> dict[str, Any]:
    workspace_source = str(repo_root / "Cargo.toml") if (repo_root / "Cargo.toml").exists() else str(repo_root)
    evidence = []
    evidence_ids: list[str] = []

    workspace_eid = "repo-root"
    evidence.append(
        make_evidence(
            evidence_id=workspace_eid,
            kind="file",
            source=workspace_source,
            summary="Repo root and layout were discovered.",
        )
    )
    evidence_ids.append(workspace_eid)

    dep_source = str(repo_root)
    dep_eid = "dependency-manifest"
    dep_status = "warn"
    dep_summary = "No recognized dependency manifest was found; runner inference came from test layout."
    dep_command = f"test -d {repo_root}"
    if (repo_root / "Cargo.toml").exists():
        dep_source = str(repo_root / "Cargo.toml")
        dep_status = "pass"
        dep_summary = "Dependency manifest was found during runner detection."
        dep_command = f"test -f {dep_source}"
    elif (repo_root / "pyproject.toml").exists():
        dep_source = str(repo_root / "pyproject.toml")
        dep_status = "pass"
        dep_summary = "Dependency manifest was found during runner detection."
        dep_command = f"test -f {dep_source}"
    elif (repo_root / "setup.py").exists():
        dep_source = str(repo_root / "setup.py")
        dep_status = "pass"
        dep_summary = "Dependency manifest was found during runner detection."
        dep_command = f"test -f {dep_source}"
    elif (repo_root / "requirements.txt").exists():
        dep_source = str(repo_root / "requirements.txt")
        dep_status = "pass"
        dep_summary = "Dependency manifest was found during runner detection."
        dep_command = f"test -f {dep_source}"
    evidence.append(
        make_evidence(
            evidence_id=dep_eid,
            kind="file",
            source=dep_source,
            summary=dep_summary,
        )
    )

    test_eid = "tests-command"
    evidence.append(
        make_evidence(
            evidence_id=test_eid,
            kind="command",
            source=" ".join(command),
            summary=f"{runner} test command executed with exit_code={exit_code}.",
        )
    )

    coverage_percent, coverage_source, coverage_note = parse_coverage_xml(repo_root)
    coverage_summary = "Coverage is not measured by this check path yet."
    coverage_eid = "coverage-measured"
    if coverage_percent is not None:
        coverage_summary = f"Measured line coverage is {coverage_percent:.1f}%."
        evidence.append(
            make_evidence(
                evidence_id=coverage_eid,
                kind="file",
                source=coverage_source or str(repo_root / "coverage.xml"),
                summary=coverage_note or f"coverage from {coverage_source}",
            )
        )
        coverage = make_coverage(
            status="measured",
            summary=coverage_summary,
            measured_percent=coverage_percent,
            evidence_ids=[coverage_eid],
        )
    else:
        coverage = make_coverage(status="coverage_unknown", summary=coverage_summary, evidence_ids=[])

    test_status = "pass" if exit_code == 0 else "fail"
    total = counts.get("total", 0)
    tests_summary = f"{runner} tests completed: {counts.get('passed', 0)} passed / {counts.get('failed', 0)} failed / total {total}."

    checks = [
        make_check(
            check_id="workspace",
            label="Workspace shape recognized",
            requirement="mandatory",
            status="pass",
            summary="Repo root exists and has been normalized.",
            evidence_ids=[workspace_eid],
            command=f"test -d {repo_root}",
        ),
        make_check(
            check_id="dependencies",
            label="Dependency/bootstrap path documented",
            requirement="mandatory",
            status=dep_status,
            summary=dep_summary,
            evidence_ids=[dep_eid],
            command=dep_command,
        ),
        make_check(
            check_id="tests",
            label="Automated validation coverage",
            requirement="mandatory",
            status=test_status,
            summary=tests_summary,
            evidence_ids=[test_eid],
            notes=f"runner={runner}; counts={json.dumps(counts, sort_keys=True)}",
            command=" ".join(command),
        ),
        make_check(
            check_id="build",
            label="Build or packaging path",
            requirement="optional",
            status="not_applicable",
            summary="Build status is not measured by this adapter.",
            evidence_ids=[],
        ),
        make_check(
            check_id="runtime",
            label="Runtime/service health",
            requirement="optional",
            status="not_applicable",
            summary="Runtime status is not measured by this adapter.",
            evidence_ids=[],
        ),
    ]

    return build_report(
        repo=make_repo(
            repo_id=repo_identity["repo_id"],
            display_name=repo_identity["display_name"],
            repo_root=repo_identity["repo_root"],
            adapter_name="central.repo_health_check",
            adapter_version="0.1.0",
            profile="library",
        ),
        checks=checks,
        coverage=coverage,
        evidence=evidence,
        headline=tests_summary,
        metadata={
            "runner": runner,
            "command": command,
            "command_text": " ".join(command),
            "test_run": {
                "runner": runner,
                "exit_code": exit_code,
                "counts": counts,
            },
        },
    )


def write_snapshot(report: dict[str, Any], ttl_seconds: int, db_path: str | None = None) -> bool:
    payload = json.dumps(report, sort_keys=True).encode()
    command = [sys.executable, str(DB_SCRIPT), "health-snapshot-write", "-", f"--ttl-seconds={ttl_seconds}"]
    if db_path:
        command.extend(["--db-path", db_path])
    result = subprocess.run(
        command,
        input=payload,
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    return result.returncode == 0


def run(repo_root: Path, ttl_seconds: int, db_path: str | None = None, timeout_seconds: int = 30) -> tuple[dict[str, Any], bool]:
    runner, command = detect_runner(repo_root)
    if runner is None:
        return {}, False
    exit_code, output, counts = run_tests(runner, command, repo_root, timeout_seconds=timeout_seconds)
    repo_identity = resolve_repo_identity(repo_root, db_path=db_path)
    report = build_health_report(repo_root, repo_identity, runner, exit_code, command, counts)
    coverage_percent, _, _ = parse_coverage_xml(repo_root)
    test_run = report.setdefault("metadata", {}).setdefault("test_run", {})
    if coverage_percent is not None:
        test_run["coverage_percent"] = round(float(coverage_percent), 2)
    return report, write_snapshot(report, ttl_seconds, db_path=db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a repo health snapshot for a single repo root.")
    parser.add_argument("repo_root", help="Repo root path.")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--timeout-seconds", type=int, default=30, help="Per-runner timeout before recording a failed snapshot.")
    parser.add_argument("--db-path", default=None, help="Optional CENTRAL DB path override.")
    parser.add_argument("--json", action="store_true", help="Print generated report JSON.")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    report, wrote = run(repo_root, ttl_seconds=args.ttl_seconds, db_path=args.db_path, timeout_seconds=args.timeout_seconds)
    if args.json and report:
        print(json.dumps(report, indent=2, sort_keys=True))
    if not report:
        return 2
    return 0 if wrote else 3


if __name__ == "__main__":
    raise SystemExit(main())
