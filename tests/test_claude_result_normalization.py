"""Regression test for Claude backend result normalization.

Root cause: ClaudeBackend runs `claude -p --output-format json` with stdout redirected
to the log file. The subprocess never writes result_path. The reaper saw no result file
and marked the task runtime_status=failed even when the claude run succeeded.

Fix: _finalize_worker calls normalize_claude_result() before the result_path.exists()
check when selected_worker_backend == "claude". The function scans the log file for the
last JSON line with type=result and writes a normalized worker_result to result_path.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from central_runtime import normalize_claude_result  # noqa: E402


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    log = tmp_path / "worker.log"
    log.write_text("\n".join(lines), encoding="utf-8")
    return log


def test_normalize_success(tmp_path: Path) -> None:
    """A successful claude -p result line produces status=COMPLETED at result_path."""
    claude_line = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Task done.",
        "session_id": "sess-abc",
        "num_turns": 3,
        "cost_usd": 0.01,
        "duration_ms": 5000,
    })
    log = _write_log(tmp_path, ["some log output", claude_line])
    result_path = tmp_path / "result.json"

    assert normalize_claude_result(log, result_path, "CENTRAL-OPS-77", "run-1") is True
    assert result_path.exists()
    payload = json.loads(result_path.read_text())
    assert payload["status"] == "COMPLETED"
    assert payload["task_id"] == "CENTRAL-OPS-77"
    assert payload["run_id"] == "run-1"
    assert "Task done." in payload["summary"]
    assert payload["validation"][0]["passed"] is True


def test_normalize_error(tmp_path: Path) -> None:
    """An is_error=True claude -p result produces status=FAILED."""
    claude_line = json.dumps({
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "result": "Something went wrong.",
        "session_id": "sess-xyz",
    })
    log = _write_log(tmp_path, [claude_line])
    result_path = tmp_path / "result.json"

    assert normalize_claude_result(log, result_path, "CENTRAL-OPS-77", "run-2") is True
    payload = json.loads(result_path.read_text())
    assert payload["status"] == "FAILED"
    assert payload["validation"][0]["passed"] is False


def test_normalize_no_result_line(tmp_path: Path) -> None:
    """If no type=result line exists, returns False and does not write result_path."""
    log = _write_log(tmp_path, ["just some log text", "no json here"])
    result_path = tmp_path / "result.json"

    assert normalize_claude_result(log, result_path, "CENTRAL-OPS-77", "run-3") is False
    assert not result_path.exists()


def test_normalize_missing_log(tmp_path: Path) -> None:
    """If log file is missing, returns False."""
    result_path = tmp_path / "result.json"
    assert normalize_claude_result(tmp_path / "no.log", result_path, "CENTRAL-OPS-77", "run-4") is False


def test_normalize_last_result_wins(tmp_path: Path) -> None:
    """If multiple type=result lines exist, the last one is used."""
    first = json.dumps({"type": "result", "is_error": True, "result": "first"})
    last = json.dumps({"type": "result", "is_error": False, "result": "last wins"})
    log = _write_log(tmp_path, [first, "middle line", last])
    result_path = tmp_path / "result.json"

    assert normalize_claude_result(log, result_path, "CENTRAL-OPS-77", "run-5") is True
    payload = json.loads(result_path.read_text())
    assert payload["status"] == "COMPLETED"
    assert "last wins" in payload["summary"]


def test_normalize_skipped_when_result_exists(tmp_path: Path) -> None:
    """normalize_claude_result is idempotent: existing result_path is overwritten only if called."""
    existing = {"status": "COMPLETED", "schema_version": 1, "task_id": "X", "run_id": "r"}
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(existing))
    # Caller (_finalize_worker) guards with `if not result_path.exists()`, so we just confirm
    # that a pre-existing file still parses correctly and normalize_claude_result would write over.
    claude_line = json.dumps({"type": "result", "is_error": False, "result": "new"})
    log = _write_log(tmp_path, [claude_line])
    assert normalize_claude_result(log, result_path, "X", "r") is True
    payload = json.loads(result_path.read_text())
    assert "new" in payload["summary"]
