"""Worker backend classes and helpers for central_runtime_v2.

Backends encapsulate how a worker subprocess is prepared and launched for a
given execution mode (codex, claude, stub).
"""

from __future__ import annotations

import abc
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from central_runtime_v2.config import (
    AUTONOMY_ROOT,
    AUTONOMY_SCHEMA_PATH,
    ActiveWorker,
    DispatcherConfig,
    ModelSelection,
    RuntimePaths,
)
from central_runtime_v2.model_policy import build_worker_task


# ---------------------------------------------------------------------------
# Late-binding import helper
# ---------------------------------------------------------------------------


def load_autonomy_runner():
    # Late-binding import: autonomy.runner lives in the Dispatcher repo, which
    # is a sibling project not installed as a package.  We must mutate sys.path
    # at call-time rather than at module import-time so that (a) the import
    # still works when AUTONOMY_ROOT changes via environment variable between
    # process start and first use, and (b) importing central_runtime_v2.backends
    # does not force a hard dependency on the Dispatcher repo being present.
    if str(AUTONOMY_ROOT) not in sys.path:
        sys.path.insert(0, str(AUTONOMY_ROOT))
    from autonomy import runner as autonomy_runner  # type: ignore

    return autonomy_runner


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def build_claude_command(worker_task: dict[str, Any], result_path: Path, model: str) -> list[str]:
    """Build a shell command that runs claude -p and converts output to worker_result schema."""
    task_id = worker_task.get("id") or worker_task.get("task_id") or "unknown"
    run_id = worker_task.get("run_id") or "unknown"
    # Stream claude output to the worker log in real-time (filtered), capture result for parsing.
    # --verbose is required for stream-json in -p mode.
    _SKIP_TYPES = "{'content_block_delta', 'input_json_delta', 'content_block_start', 'content_block_stop'}"
    script = (
        "import json, subprocess, sys\n"
        f"proc = subprocess.Popen(\n"
        f"    ['claude', '-p', '--verbose', '--dangerously-skip-permissions', '--model', {model!r}, '--output-format', 'stream-json'],\n"
        "    stdin=sys.stdin, stdout=subprocess.PIPE, text=True\n"
        ")\n"
        "lines = []\n"
        "for _line in proc.stdout:\n"
        "    _stripped = _line.rstrip()\n"
        "    if not _stripped: continue\n"
        "    lines.append(_stripped)\n"
        "    try:\n"
        "        _ev = json.loads(_stripped)\n"
        f"        if _ev.get('type') not in {_SKIP_TYPES}:\n"
        "            sys.stdout.write(_line); sys.stdout.flush()\n"
        "    except Exception:\n"
        "        sys.stdout.write(_line); sys.stdout.flush()\n"
        "proc.wait()\n"
        "claude_result = {}\n"
        "for _line in reversed(lines):\n"
        "    try:\n"
        "        _obj = json.loads(_line)\n"
        "        if _obj.get('type') == 'result':\n"
        "            claude_result = _obj; break\n"
        "    except Exception: pass\n"
        "is_error = claude_result.get('is_error', False) or claude_result.get('type') == 'error'\n"
        "summary = str(claude_result.get('result', '') or claude_result.get('error', {}).get('message', 'no result'))[:2000]\n"
        "_sl = summary.lower()\n"
        "_verdict = 'accepted'\n"
        "if 'rework_required' in _sl: _verdict = 'rework_required'\n"
        "else:\n"
        "  _vi = _sl.find('verdict')\n"
        "  if _vi >= 0 and ('fail' in _sl[_vi:_vi+80] or '\\u274c' in summary[_vi:_vi+80]): _verdict = 'rework_required'\n"
        "payload = {\n"
        "    'schema_version': 1,\n"
        f"    'task_id': {task_id!r},\n"
        f"    'run_id': {run_id!r},\n"
        "    'status': 'FAILED' if is_error or proc.returncode != 0 else 'COMPLETED',\n"
        "    'summary': summary,\n"
        "    'completed_items': [summary] if not is_error else [],\n"
        "    'remaining_items': [],\n"
        "    'decisions': [],\n"
        "    'discoveries': [],\n"
        "    'blockers': [],\n"
        "    'validation': [],\n"
        "    'verdict': _verdict,\n"
        "    'requirements_assessment': [],\n"
        "    'system_fit_assessment': {},\n"
        "    'capability_mutation': None,\n"
        "    'files_changed': [],\n"
        "    'warnings': [],\n"
        "    'artifacts': [],\n"
        "    'claude_raw': claude_result,\n"
        "}\n"
        "from pathlib import Path\n"
        f"Path({str(result_path)!r}).write_text(json.dumps(payload, indent=2), encoding='utf-8')\n"
        "print(json.dumps({'status': payload['status'], 'summary_preview': summary[:200]}))\n"
        "sys.exit(proc.returncode)\n"
    )
    return [sys.executable, "-c", script]


def build_stub_command(snapshot: dict[str, Any], run_id: str, result_path: Path) -> list[str]:
    execution_metadata = (snapshot.get("execution") or {}).get("metadata") or {}
    sleep_seconds = float(
        execution_metadata.get(
            "stub_sleep_seconds",
            os.environ.get("CENTRAL_STUB_WORKER_SECONDS", "0.5"),
        )
        or 0.5
    )
    log_interval_seconds = float(
        execution_metadata.get(
            "stub_log_interval_seconds", min(max(sleep_seconds / 4.0, 0.2), 1.0)
        )
        or 0.2
    )
    code = (
        "import json,sys,time;"
        "from pathlib import Path;"
        "task_id,run_id,result_path,sleep_seconds,log_interval=sys.argv[1:6];"
        "sleep_seconds=float(sleep_seconds);"
        "log_interval=max(float(log_interval),0.05);"
        "steps=max(1,int(sleep_seconds/log_interval));"
        "remaining=max(0.0,sleep_seconds-(steps*log_interval));"
        "print(f'stub worker starting task={task_id} run={run_id}', flush=True);"
        "[(print(f'stub progress {index+1}/{steps}', flush=True), time.sleep(log_interval)) for index in range(steps)];"
        "time.sleep(remaining);"
        "payload={"
        "'status':'COMPLETED',"
        "'schema_version':1,"
        "'task_id':task_id,"
        "'run_id':run_id,"
        "'summary':'stub worker completed',"
        "'completed_items':['stub execution completed'],"
        "'remaining_items':[],"
        "'decisions':['used stub worker mode'],"
        "'discoveries':[],"
        "'blockers':[],"
        "'validation':[{'name':'stub-mode','passed':True,'notes':'synthetic worker result'}],"
        "'verdict':'accepted',"
        "'requirements_assessment':[],"
        "'system_fit_assessment':{},"
        "'capability_mutation':None,"
        "'files_changed':[],"
        "'warnings':[],"
        "'artifacts':[]"
        "};"
        "Path(result_path).write_text(json.dumps(payload), encoding='utf-8')"
    )
    return [
        sys.executable,
        "-c",
        code,
        snapshot["task_id"],
        run_id,
        str(result_path),
        str(sleep_seconds),
        str(log_interval_seconds),
    ]


# ---------------------------------------------------------------------------
# normalize_claude_result
# ---------------------------------------------------------------------------


def normalize_claude_result(log_path: Path, result_path: Path, task_id: str, run_id: str) -> bool:
    """Parse claude -p JSON output from log_path and write normalized worker_result to result_path.

    claude -p --output-format json writes a final JSON line to stdout of the form:
        {"type": "result", "subtype": "success", "is_error": false, "result": "...", ...}

    The reaper expects result_path to contain worker_result.schema.json format:
        {"status": "COMPLETED", "schema_version": 1, "task_id": "...", "summary": "...", ...}

    Returns True if a result line was found and written, False otherwise.
    """
    if not log_path.exists():
        return False
    claude_result: dict[str, Any] | None = None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "result":
                    claude_result = obj
                    break
            except json.JSONDecodeError:
                continue
    except Exception:
        return False
    if claude_result is None:
        return False
    is_error = bool(claude_result.get("is_error"))
    raw_result = str(claude_result.get("result") or "")
    status = "FAILED" if is_error else "COMPLETED"
    payload: dict[str, Any] = {
        "status": status,
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "summary": raw_result[:2000]
        if raw_result
        else ("claude worker error" if is_error else "claude worker completed"),
        "completed_items": [] if is_error else ["claude worker run finished"],
        "remaining_items": [],
        "decisions": [],
        "discoveries": [],
        "blockers": [],
        "validation": [
            {"name": "claude-exit", "passed": not is_error, "notes": f"is_error={is_error}"}
        ],
        "capability_mutation": None,
        "files_changed": [],
        "warnings": [],
        "artifacts": [],
        "_claude_meta": {
            "subtype": claude_result.get("subtype"),
            "session_id": claude_result.get("session_id"),
            "num_turns": claude_result.get("num_turns"),
            "cost_usd": claude_result.get("cost_usd"),
            "duration_ms": claude_result.get("duration_ms"),
        },
    }
    try:
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Backend classes
# ---------------------------------------------------------------------------


class WorkerBackend(abc.ABC):
    """Protocol for backend-specific worker spawn preparation."""

    @abc.abstractmethod
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        """Return (prompt_text, command, stdin_mode) for subprocess.Popen."""


class CodexBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        worker_task["run_id"] = run_id
        autonomy_runner = load_autonomy_runner()
        prompt_text = autonomy_runner.build_prompt(
            worker_task,
            autonomy_runner.normalize_dependency_context(
                [
                    {
                        "task_id": dep.get("depends_on_task_id"),
                        "title": dep.get("depends_on_title"),
                        "summary": dep.get("depends_on_status"),
                        "decisions": "",
                        "blockers": "",
                        "validation": "",
                    }
                    for dep in snapshot.get("dependencies") or []
                ]
            ),
        )
        command = autonomy_runner.build_codex_command(worker_task, result_path, AUTONOMY_SCHEMA_PATH)
        return prompt_text, command, subprocess.PIPE


class ClaudeBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        command = build_claude_command(worker_task, result_path, worker_task["worker_model"])
        return prompt_text, command, subprocess.PIPE


class StubBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        command = build_stub_command(snapshot, run_id, result_path)
        return prompt_text, command, subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_WORKER_BACKENDS: dict[str, WorkerBackend] = {
    "codex": CodexBackend(),
    "claude": ClaudeBackend(),
    "stub": StubBackend(),
}


def get_worker_backend(name: str) -> WorkerBackend:
    """Return the WorkerBackend for the given backend name, defaulting to StubBackend."""
    return _WORKER_BACKENDS.get(name, _WORKER_BACKENDS["stub"])
