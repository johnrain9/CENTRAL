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

import central_task_db as task_db
from central_runtime_v2.config import (
    AUTONOMY_ROOT,
    AUTONOMY_SCHEMA_PATH,
    ActiveWorker,
    DEFAULT_DB_PATH,
    DispatcherConfig,
    ModelSelection,
    RuntimePaths,
)
from central_runtime_v2.model_policy import build_worker_task
import session_manager


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


def build_claude_command(
    worker_task: dict[str, Any],
    result_path: Path,
    model: str,
    *,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a shell command that runs claude -p and converts output to worker_result schema."""
    task_id = worker_task.get("id") or worker_task.get("task_id") or "unknown"
    run_id = worker_task.get("run_id") or "unknown"
    effort = worker_task.get("worker_effort") or None
    # Read schema for --json-schema flag so Claude outputs structured JSON directly.
    try:
        _schema_str = AUTONOMY_SCHEMA_PATH.read_text(encoding="utf-8").strip()
        import json as _j; _j.loads(_schema_str)  # validate
    except Exception:
        _schema_str = ""
    # Stream claude output to the worker log in real-time (filtered), capture result for parsing.
    # --verbose is required for stream-json in -p mode.
    _SKIP_TYPES = "{'content_block_delta', 'input_json_delta', 'content_block_start', 'content_block_stop'}"
    _schema_arg = f", '--json-schema', {_schema_str!r}" if _schema_str else ""
    _effort_arg = f", '--effort', {effort!r}" if effort else ""
    _extra_args = extra_args or []
    script = (
        "import json, subprocess, sys\n"
        f"cmd = ['claude', '-p', '--verbose', '--dangerously-skip-permissions', '--model', {model!r}, '--output-format', 'stream-json'{_schema_arg}{_effort_arg}]\n"
        f"cmd.extend({_extra_args!r})\n"
        f"proc = subprocess.Popen(\n"
        f"    cmd,\n"
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
        "# Collect all result events; prefer the one with structured_output (StructuredOutput tool\n"
        "# call from any session) so that sub-agent follow-on sessions do not shadow the main\n"
        "# session's StructuredOutput call.\n"
        "_all_results = []\n"
        "for _line in lines:\n"
        "    try:\n"
        "        _obj = json.loads(_line)\n"
        "        if _obj.get('type') == 'result':\n"
        "            _all_results.append(_obj)\n"
        "    except Exception: pass\n"
        "claude_result = {}\n"
        "for _r in _all_results:\n"
        "    _so = _r.get('structured_output')\n"
        "    if isinstance(_so, dict) and 'schema_version' in _so:\n"
        "        claude_result = _r; break\n"
        "if not claude_result:\n"
        "    claude_result = _all_results[-1] if _all_results else {}\n"
        "is_error = claude_result.get('is_error', False) or claude_result.get('type') == 'error'\n"
        "result_text = str(claude_result.get('result', '') or claude_result.get('error', {}).get('message', 'no result'))\n"
        "# Try to parse structured JSON output from --json-schema.\n"
        "# Claude puts it in 'structured_output' on the result event (result field is empty).\n"
        "_structured = {}\n"
        "_so = claude_result.get('structured_output')\n"
        "if isinstance(_so, dict) and 'schema_version' in _so:\n"
        "    _structured = _so\n"
        "else:\n"
        "    try:\n"
        "        _parsed = json.loads(result_text)\n"
        "        if isinstance(_parsed, dict) and 'schema_version' in _parsed:\n"
        "            _structured = _parsed\n"
        "    except Exception: pass\n"
        "summary = str(_structured.get('summary', result_text))[:2000]\n"
        "_sl = summary.lower()\n"
        "_verdict = _structured.get('verdict') or None\n"
        "if not _verdict:\n"
        "    _verdict = 'accepted'\n"
        "    if 'rework_required' in _sl: _verdict = 'rework_required'\n"
        "    else:\n"
        "        _vi = _sl.find('verdict')\n"
        "        if _vi >= 0 and ('fail' in _sl[_vi:_vi+80] or '\\u274c' in summary[_vi:_vi+80]): _verdict = 'rework_required'\n"
        "_usage = claude_result.get('usage') or {}\n"
        "_inp = int(_usage.get('input_tokens') or 0)\n"
        "_out = int(_usage.get('output_tokens') or 0)\n"
        "_cache_read = int(_usage.get('cache_read_input_tokens') or 0)\n"
        "_cache_write = int(_usage.get('cache_creation_input_tokens') or 0)\n"
        "_tokens_used = (_inp + _out + _cache_read + _cache_write) or None\n"
        "_tokens_cost = claude_result.get('total_cost_usd') or claude_result.get('cost_usd') or None\n"
        "payload = {\n"
        "    'schema_version': 2,\n"
        f"    'task_id': {task_id!r},\n"
        f"    'run_id': {run_id!r},\n"
        "    'status': 'FAILED' if is_error or proc.returncode != 0 else 'COMPLETED',\n"
        "    'summary': summary,\n"
        "    'completed_items': _structured.get('completed_items', [summary] if not is_error else []),\n"
        "    'remaining_items': _structured.get('remaining_items', []),\n"
        "    'decisions': _structured.get('decisions', []),\n"
        "    'discoveries': _structured.get('discoveries', []),\n"
        "    'blockers': _structured.get('blockers', []),\n"
        "    'validation': _structured.get('validation', []),\n"
        "    'verdict': _verdict,\n"
        "    'requirements_assessment': _structured.get('requirements_assessment', []),\n"
        "    'system_fit_assessment': _structured.get('system_fit_assessment', {}),\n"
        "    'capability_mutation': _structured.get('capability_mutation', None),\n"
        "    'files_changed': _structured.get('files_changed', []),\n"
        "    'warnings': _structured.get('warnings', []),\n"
        "    'artifacts': _structured.get('artifacts', []),\n"
        "    'tokens_used': _tokens_used,\n"
        "    'tokens_cost_usd': _tokens_cost,\n"
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
    all_results: list[dict[str, Any]] = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "result":
                    all_results.append(obj)
            except json.JSONDecodeError:
                continue
    except Exception:
        return False
    if not all_results:
        return False
    # Prefer the result event that carries structured_output (StructuredOutput tool call from
    # any session in the worker's session tree).  Sub-agent follow-on sessions emit their own
    # result events after the main session; without this preference the last (empty) sub-agent
    # result would shadow the main session's StructuredOutput payload.
    claude_result: dict[str, Any] = {}
    for r in all_results:
        so = r.get("structured_output")
        if isinstance(so, dict) and "schema_version" in so:
            claude_result = r
            break
    if not claude_result:
        claude_result = all_results[-1]
    is_error = bool(claude_result.get("is_error"))
    _usage = claude_result.get("usage") or {}
    _inp = int(_usage.get("input_tokens") or 0)
    _out = int(_usage.get("output_tokens") or 0)
    _cache_read = int(_usage.get("cache_read_input_tokens") or 0)
    _cache_write = int(_usage.get("cache_creation_input_tokens") or 0)
    tokens_used: int | None = (_inp + _out + _cache_read + _cache_write) or None
    tokens_cost_usd: float | None = claude_result.get("total_cost_usd") or claude_result.get("cost_usd") or None
    # If the preferred result event carries a full structured_output payload, use it directly
    # (augmented with metadata) rather than synthesising a minimal payload from the text result.
    _so = claude_result.get("structured_output")
    if isinstance(_so, dict) and "schema_version" in _so:
        payload: dict[str, Any] = dict(_so)
        payload["task_id"] = task_id
        payload["run_id"] = run_id
        if tokens_used is not None:
            payload["tokens_used"] = tokens_used
        if tokens_cost_usd is not None:
            payload["tokens_cost_usd"] = tokens_cost_usd
        payload["_claude_meta"] = {
            "subtype": claude_result.get("subtype"),
            "session_id": claude_result.get("session_id"),
            "num_turns": claude_result.get("num_turns"),
            "cost_usd": tokens_cost_usd,
            "duration_ms": claude_result.get("duration_ms"),
        }
    else:
        raw_result = str(claude_result.get("result") or "")
        status = "FAILED" if is_error else "COMPLETED"
        payload = {
            "status": status,
            "schema_version": 2,
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
            "tokens_used": tokens_used,
            "tokens_cost_usd": tokens_cost_usd,
            "_claude_meta": {
                "subtype": claude_result.get("subtype"),
                "session_id": claude_result.get("session_id"),
                "num_turns": claude_result.get("num_turns"),
                "cost_usd": tokens_cost_usd,
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

    def env_overrides(self) -> dict[str, str]:
        """Return extra env vars to inject into the worker subprocess environment."""
        return {}


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
        worker_task["run_id"] = run_id
        prompt_text = worker_task["prompt_body"]
        db_path = Path(worker_task.get("db_path") or DEFAULT_DB_PATH)
        session_focus = str((snapshot.get("metadata") or {}).get("session_focus") or "")
        fork_result = session_manager.get_fork_args(snapshot["target_repo_id"], db_path, focus=session_focus)
        command = build_claude_command(
            worker_task,
            result_path,
            worker_task["worker_model"],
            extra_args=fork_result.args if fork_result else None,
        )
        if fork_result:
            task_id = str(snapshot.get("task_id") or worker_task.get("task_id") or "")
            if task_id:
                self._log_session_fork(task_id, snapshot["target_repo_id"], db_path, fork_result)
        return prompt_text, command, subprocess.PIPE

    def _log_session_fork(
        self,
        task_id: str,
        repo_id: str,
        db_path: Path,
        result: session_manager.SessionForkResult,
    ) -> None:
        try:
            conn = task_db.connect(db_path)
        except Exception:
            return
        try:
            fork_count: int | None = None
            row = conn.execute(
                "SELECT fork_count FROM session_registry WHERE session_id = ?",
                (result.session_id,),
            ).fetchone()
            if row is not None:
                fork_count = int(row["fork_count"] or 0)
            task_db.insert_event(
                conn,
                task_id=task_id,
                event_type="session.forked",
                actor_kind="runtime",
                actor_id="central.dispatcher",
                payload={
                    "repo_id": repo_id,
                    "session_id": result.session_id,
                    "focus": result.focus,
                    "fork_count": fork_count,
                    "stale": result.stale,
                },
            )
            if result.stale:
                task_db.insert_event(
                    conn,
                    task_id=task_id,
                    event_type="session.stale_detected",
                    actor_kind="runtime",
                    actor_id="central.dispatcher",
                    payload={
                        "repo_id": repo_id,
                        "session_id": result.session_id,
                        "reason": result.stale_reason,
                    },
                )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


def build_gemini_command(worker_task: dict[str, Any], result_path: Path, model: str) -> list[str]:
    """Build a shell command that runs gemini -p and converts output to worker_result schema."""
    task_id = worker_task.get("id") or worker_task.get("task_id") or "unknown"
    run_id = worker_task.get("run_id") or "unknown"
    _VERDICT_LOGIC = (
        "_verdict = _structured.get('verdict') or None\n"
        "if not _verdict:\n"
        "    _verdict = 'accepted'\n"
        "    if 'rework_required' in _sl: _verdict = 'rework_required'\n"
        "    else:\n"
        "        _vi = _sl.find('verdict')\n"
        "        if _vi >= 0 and ('fail' in _sl[_vi:_vi+80] or '\\u274c' in summary[_vi:_vi+80]): _verdict = 'rework_required'\n"
    )
    script = (
        "import json, subprocess, sys\n"
        "prompt = sys.stdin.read()\n"
        # Pipe prompt via stdin; use -p ' ' as minimal headless trigger (gemini prepends stdin to -p)
        f"proc = subprocess.Popen(\n"
        f"    ['gemini', '--model', {model!r}, '-y', '--output-format', 'json', '-p', ' '],\n"
        "    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True\n"
        ")\n"
        "raw_output, _ = proc.communicate(input=prompt)\n"
        # Parse JSON: try full output first, then find first '{' to skip leading status lines
        "gemini_json = {}\n"
        "try:\n"
        "    gemini_json = json.loads(raw_output)\n"
        "except Exception:\n"
        "    _idx = (raw_output or '').find('{')\n"
        "    if _idx >= 0:\n"
        "        try: gemini_json = json.loads(raw_output[_idx:])\n"
        "        except Exception: pass\n"
        "response_text = str(gemini_json.get('response', raw_output or ''))[:50000]\n"
        # Detect errors: returncode nonzero, or no JSON response and output looks like an error
        "_error_signals = ('429', 'rateLimitExceeded', 'MODEL_CAPACITY_EXHAUSTED', 'RESOURCE_EXHAUSTED')\n"
        "is_error = proc.returncode != 0 or (not gemini_json.get('response') and any(s in raw_output for s in _error_signals))\n"
        # Extract structured JSON — strip markdown code fences if present
        "_structured = {}\n"
        "import re as _re\n"
        "_json_text = response_text\n"
        "_fence_match = _re.search(r'```(?:json)?\\s*({[\\s\\S]*?})\\s*```', _json_text)\n"
        "if _fence_match: _json_text = _fence_match.group(1)\n"
        "try:\n"
        "    _parsed = json.loads(_json_text)\n"
        "    if isinstance(_parsed, dict) and 'schema_version' in _parsed:\n"
        "        _structured = _parsed\n"
        "except Exception: pass\n"
        "summary = str(_structured.get('summary', response_text))[:2000]\n"
        "_sl = summary.lower()\n"
        + _VERDICT_LOGIC +
        "payload = {\n"
        "    'schema_version': 1,\n"
        f"    'task_id': {task_id!r},\n"
        f"    'run_id': {run_id!r},\n"
        "    'status': 'FAILED' if is_error else 'COMPLETED',\n"
        "    'summary': summary,\n"
        "    'completed_items': _structured.get('completed_items', [summary] if not is_error else []),\n"
        "    'remaining_items': _structured.get('remaining_items', []),\n"
        "    'decisions': _structured.get('decisions', []),\n"
        "    'discoveries': _structured.get('discoveries', []),\n"
        "    'blockers': _structured.get('blockers', []),\n"
        "    'validation': _structured.get('validation', []),\n"
        "    'verdict': _verdict,\n"
        "    'requirements_assessment': _structured.get('requirements_assessment', []),\n"
        "    'system_fit_assessment': _structured.get('system_fit_assessment', {}),\n"
        "    'capability_mutation': _structured.get('capability_mutation', None),\n"
        "    'files_changed': _structured.get('files_changed', []),\n"
        "    'warnings': _structured.get('warnings', []),\n"
        "    'artifacts': _structured.get('artifacts', []),\n"
        "    'gemini_raw': {'session_id': gemini_json.get('session_id'), 'stats': gemini_json.get('stats', {})},\n"
        "}\n"
        "from pathlib import Path\n"
        f"Path({str(result_path)!r}).write_text(json.dumps(payload, indent=2), encoding='utf-8')\n"
        "print(json.dumps({'status': payload['status'], 'summary_preview': summary[:200]}))\n"
        "sys.exit(proc.returncode)\n"
    )
    return [sys.executable, "-c", script]


class GeminiBackend(WorkerBackend):
    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        command = build_gemini_command(worker_task, result_path, worker_task["worker_model"])
        return prompt_text, command, subprocess.PIPE


class GrokBackend(WorkerBackend):
    """Calls xAI Grok API via a custom tool-use loop (grok_worker.py).

    xAI only implements Chat Completions (/v1/chat/completions), not the
    OpenAI Responses API that the codex CLI requires, so we run our own
    agentic loop with read_file/write_file/bash tools.
    """

    def prepare(
        self,
        snapshot: dict[str, Any],
        worker_task: dict[str, Any],
        run_id: str,
        result_path: Path,
    ) -> tuple[str, list[str], Any]:
        prompt_text = worker_task["prompt_body"]
        task_id = worker_task.get("id") or worker_task.get("task_id") or "unknown"
        model = worker_task.get("worker_model") or "grok-4-1-fast-reasoning"
        repo_root = worker_task.get("repo_root", ".")
        script_path = Path(__file__).parent / "grok_worker.py"
        command = [
            sys.executable,
            str(script_path),
            model,
            task_id,
            run_id,
            str(result_path),
            str(AUTONOMY_SCHEMA_PATH),
            repo_root,
        ]
        return prompt_text, command, subprocess.PIPE

    def env_overrides(self) -> dict[str, str]:
        return {}  # GROK_API_KEY passes through from dispatcher env unchanged


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
    "gemini": GeminiBackend(),
    "grok": GrokBackend(),
    "stub": StubBackend(),
}


def get_worker_backend(name: str) -> WorkerBackend:
    """Return the WorkerBackend for the given backend name, defaulting to StubBackend."""
    return _WORKER_BACKENDS.get(name, _WORKER_BACKENDS["stub"])
