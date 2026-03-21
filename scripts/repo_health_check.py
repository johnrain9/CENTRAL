#!/usr/bin/env python3
"""Collect test counts/coverage for a repo and persist a repo-health snapshot."""

from __future__ import annotations

import argparse
import dis
import json
import os
import pickle
import re
import shutil
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


def _collect_executable_lines(code_obj: Any, bucket: set[int]) -> None:
    for _, line in dis.findlinestarts(code_obj):
        if line and line > 0:
            bucket.add(line)
    for constant in code_obj.co_consts:
        if hasattr(constant, "co_consts") and hasattr(constant, "co_firstlineno"):
            _collect_executable_lines(constant, bucket)


def _iter_repo_python_files(repo_root: Path) -> list[Path]:
    excluded_roots = {".git", ".venv", ".pytest_cache", "__pycache__", "state", "generated"}
    files: list[Path] = []
    for path in repo_root.rglob("*.py"):
        rel_parts = path.relative_to(repo_root).parts
        if any(part in excluded_roots for part in rel_parts):
            continue
        files.append(path)
    return files


def write_trace_coverage_xml(repo_root: Path, trace_counts_path: Path) -> float | None:
    if not trace_counts_path.exists():
        return None
    try:
        with trace_counts_path.open("rb") as handle:
            loaded = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError):
        return None
    if not isinstance(loaded, tuple) or not loaded:
        return None
    counts = loaded[0]
    if not isinstance(counts, dict):
        return None

    executable_by_file: dict[str, set[int]] = {}
    total_valid = 0
    total_covered = 0

    for py_file in _iter_repo_python_files(repo_root):
        filename = str(py_file.resolve())
        try:
            source = py_file.read_text(encoding="utf-8")
            code = compile(source, filename, "exec")
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        executable_lines: set[int] = set()
        _collect_executable_lines(code, executable_lines)
        if not executable_lines:
            continue
        executable_by_file[filename] = executable_lines
        total_valid += len(executable_lines)

    if total_valid == 0:
        return None

    for key, value in counts.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        filename, line_no = key
        if not isinstance(filename, str) or not isinstance(line_no, int):
            continue
        if value <= 0:
            continue
        exec_lines = executable_by_file.get(str(Path(filename).resolve()))
        if not exec_lines or line_no not in exec_lines:
            continue
        total_covered += 1

    line_rate = total_covered / total_valid if total_valid else 0.0
    coverage_xml = repo_root / "coverage.xml"
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<coverage line-rate="{line_rate:.6f}" lines-covered="{total_covered}" lines-valid="{total_valid}"/>\n'
    )
    coverage_xml.write_text(payload, encoding="utf-8")
    return line_rate * 100.0


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


def _module_available(python_executable: str, module_name: str) -> bool:
    command = [
        python_executable,
        "-c",
        (
            "import importlib.util, sys; "
            f"sys.exit(0 if importlib.util.find_spec('{module_name}') is not None else 1)"
        ),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _python_candidates(repo_root: Path) -> list[str]:
    candidates: list[str] = [sys.executable]
    repo_venv_python = repo_root / ".venv" / "bin" / "python"
    if repo_venv_python.exists():
        repo_venv = str(repo_venv_python.resolve())
        if repo_venv not in candidates:
            candidates.append(repo_venv)
    return candidates


def _read_min_coverage_from_pyproject(repo_root: Path) -> float | None:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        import tomllib

        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return None
    value = (((payload.get("tool") or {}).get("repo_health_check") or {}).get("min_coverage"))
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 100:
        return 100.0
    return parsed


def resolve_min_coverage(repo_root: Path, cli_min_coverage: float | None) -> float | None:
    if cli_min_coverage is not None:
        return max(0.0, min(100.0, float(cli_min_coverage)))
    env_value = os.environ.get("REPO_HEALTH_MIN_COVERAGE")
    if env_value:
        try:
            parsed = float(env_value)
            return max(0.0, min(100.0, parsed))
        except ValueError:
            return None
    return _read_min_coverage_from_pyproject(repo_root)


def detect_runner(repo_root: Path, *, min_coverage: float | None = None) -> tuple[str | None, list[str]]:
    python_manifest = (
        (repo_root / "pyproject.toml").exists()
        or (repo_root / "setup.py").exists()
        or (repo_root / "setup.cfg").exists()
        or (repo_root / "requirements.txt").exists()
        or any(repo_root.glob("*.py"))
    )
    if (repo_root / "scripts" / "tests").is_dir() or python_manifest:
        for python_exec in _python_candidates(repo_root):
            if not _module_available(python_exec, "pytest"):
                continue
            command = [python_exec, "-m", "pytest", "-x"]
            if _module_available(python_exec, "pytest_cov"):
                command.extend(["--cov=.", "--cov-report=xml:coverage.xml"])
                if min_coverage is not None:
                    command.append(f"--cov-fail-under={min_coverage:.1f}")
            else:
                command = [
                    python_exec,
                    "-m",
                    "trace",
                    "--count",
                    "--file",
                    str(repo_root / ".repo_health_trace_counts.pkl"),
                    "--coverdir",
                    str(repo_root / ".repo_health_trace_reports"),
                    "--module",
                    "pytest",
                    "-x",
                ]
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
    summary_line = ""
    # Typical summary line:
    # "= 2 failed, 4 passed, 1 skipped in 0.12s ="
    # Trace-wrapped pytest runs can include additional prefix text.
    for line in reversed(output.splitlines()):
        text = line.strip().strip("=")
        if " in " in text and any(tag in text for tag in (" passed", " failed", " skipped", " xpassed", " xfailed", " deselected", " error")):
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
    if "trace" in command and exit_code == 0 and (counts.get("failed", 0) > 0 or counts.get("error", 0) > 0):
        # `python -m trace` can return 0 even when pytest fails; align status with parsed test result.
        exit_code = 1
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


def run(
    repo_root: Path,
    ttl_seconds: int,
    db_path: str | None = None,
    timeout_seconds: int = 30,
    min_coverage: float | None = None,
) -> tuple[dict[str, Any], bool]:
    resolved_min_coverage = resolve_min_coverage(repo_root, min_coverage)
    runner, command = detect_runner(repo_root, min_coverage=resolved_min_coverage)
    if runner is None:
        return {}, False
    trace_counts_path = repo_root / ".repo_health_trace_counts.pkl"
    trace_report_dir = repo_root / ".repo_health_trace_reports"
    if "--file" in command and "trace" in command and trace_counts_path.exists():
        trace_counts_path.unlink(missing_ok=True)
    exit_code, output, counts = run_tests(runner, command, repo_root, timeout_seconds=timeout_seconds)
    if "--file" in command and "trace" in command:
        write_trace_coverage_xml(repo_root, trace_counts_path)
        trace_counts_path.unlink(missing_ok=True)
        shutil.rmtree(trace_report_dir, ignore_errors=True)
    repo_identity = resolve_repo_identity(repo_root, db_path=db_path)
    report = build_health_report(repo_root, repo_identity, runner, exit_code, command, counts)
    coverage_percent, _, _ = parse_coverage_xml(repo_root)
    if coverage_percent is not None and resolved_min_coverage is not None and coverage_percent < resolved_min_coverage and exit_code == 0:
        exit_code = 2
        report["headline"] = (
            f"{report.get('headline', '')} Coverage {coverage_percent:.1f}% is below threshold {resolved_min_coverage:.1f}%."
        ).strip()
        for check in report.get("checks", []):
            if check.get("check_id") == "tests":
                check["status"] = "fail"
                check["summary"] = (
                    f"{check.get('summary', '')} Coverage {coverage_percent:.1f}% is below threshold {resolved_min_coverage:.1f}%."
                ).strip()
                notes = str(check.get("notes", ""))
                check["notes"] = f"{notes}; coverage_threshold_failed=true".strip("; ")
                break
    test_run = report.setdefault("metadata", {}).setdefault("test_run", {})
    test_run["exit_code"] = exit_code
    if coverage_percent is not None:
        test_run["coverage_percent"] = round(float(coverage_percent), 2)
    if resolved_min_coverage is not None:
        test_run["coverage_threshold_percent"] = round(float(resolved_min_coverage), 2)
    return report, write_snapshot(report, ttl_seconds, db_path=db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a repo health snapshot for a single repo root.")
    parser.add_argument("repo_root", help="Repo root path.")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--timeout-seconds", type=int, default=30, help="Per-runner timeout before recording a failed snapshot.")
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=None,
        help="Optional minimum line coverage percentage for pytest-cov (0-100). Can also be set via REPO_HEALTH_MIN_COVERAGE.",
    )
    parser.add_argument("--db-path", default=None, help="Optional CENTRAL DB path override.")
    parser.add_argument("--json", action="store_true", help="Print generated report JSON.")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    report, wrote = run(
        repo_root,
        ttl_seconds=args.ttl_seconds,
        db_path=args.db_path,
        timeout_seconds=args.timeout_seconds,
        min_coverage=args.min_coverage,
    )
    if args.json and report:
        print(json.dumps(report, indent=2, sort_keys=True))
    if not report:
        return 2
    return 0 if wrote else 3


if __name__ == "__main__":
    raise SystemExit(main())
