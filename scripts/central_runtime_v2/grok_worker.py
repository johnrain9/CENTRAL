"""Grok worker subprocess — agentic tool-use loop via xAI Chat Completions API.

Called by GrokBackend.prepare():
    python3 grok_worker.py <model> <task_id> <run_id> <result_path> <schema_path> <repo_root>

Reads prompt from stdin. Runs a multi-turn tool-use loop (read_file, write_file, bash)
against api.x.ai/v1/chat/completions until the model produces a final JSON result.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI-compatible function schema)
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file relative to the repo root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to repo root"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates parent directories if needed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to repo root"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the repo root. Returns stdout + stderr (truncated to 8000 chars).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


def _exec_read_file(args: dict, repo_root: Path) -> str:
    path = repo_root / args["path"]
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"


def _exec_write_file(args: dict, repo_root: Path) -> str:
    path = repo_root / args["path"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return f"OK: wrote {len(args['content'])} bytes to {args['path']}"
    except Exception as e:
        return f"ERROR: {e}"


def _exec_bash(args: dict, repo_root: Path) -> str:
    try:
        result = subprocess.run(
            args["command"],
            shell=True,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out[:8000] if out else f"(exit code {result.returncode})"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s"
    except Exception as e:
        return f"ERROR: {e}"


def _dispatch_tool(name: str, args: dict, repo_root: Path) -> str:
    if name == "read_file":
        return _exec_read_file(args, repo_root)
    if name == "write_file":
        return _exec_write_file(args, repo_root)
    if name == "bash":
        return _exec_bash(args, repo_root)
    return f"ERROR: unknown tool {name!r}"


# ---------------------------------------------------------------------------
# Result extraction helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```")


def _extract_json(text: str) -> dict:
    for m in _FENCE_RE.finditer(text):
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict) and "schema_version" in parsed:
                return parsed
        except Exception:
            pass
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _infer_verdict(structured: dict, summary: str) -> str:
    if structured.get("verdict"):
        return structured["verdict"]
    sl = summary.lower()
    if "rework_required" in sl:
        return "rework_required"
    vi = sl.find("verdict")
    if vi >= 0 and ("fail" in sl[vi:vi + 80] or "\u274c" in summary[vi:vi + 80]):
        return "rework_required"
    return "accepted"


# ---------------------------------------------------------------------------
# Pricing (per 1M tokens)
# ---------------------------------------------------------------------------

_PRICING: dict[str, tuple[float, float]] = {
    "grok-3":                  (3.00, 15.00),
    "grok-3-fast":             (0.60,  4.00),
    "grok-3-mini":             (0.30,  0.50),
    "grok-3-mini-fast":        (0.10,  0.20),
    "grok-4":                  (3.00, 15.00),
    "grok-4-1":                (3.00, 15.00),
    "grok-4-1-fast-reasoning": (0.20,  0.50),
    "grok-4-1-fast-non-reasoning": (0.20, 0.50),
    "grok-4.20-0309-reasoning":    (2.00,  6.00),
    "grok-4.20-0309-non-reasoning":(2.00,  6.00),
    "grok-4.20-multi-agent-0309":  (2.00,  6.00),
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 7:
        print(
            "Usage: grok_worker.py <model> <task_id> <run_id> <result_path> <schema_path> <repo_root>",
            file=sys.stderr,
        )
        sys.exit(1)

    model = sys.argv[1]
    task_id = sys.argv[2]
    run_id = sys.argv[3]
    result_path = Path(sys.argv[4])
    schema_path = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    repo_root = Path(sys.argv[6]).expanduser().resolve() if len(sys.argv) > 6 else Path.cwd()

    prompt = sys.stdin.read()

    api_key = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY", "")
    if not api_key:
        print("ERROR: GROK_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    # System prompt: instruct to use tools and return JSON
    schema_instruction = ""
    if schema_path and schema_path.exists():
        try:
            schema_text = schema_path.read_text(encoding="utf-8").strip()
            json.loads(schema_text)  # validate
            schema_instruction = (
                "\n\nWhen you have completed all work, return your final answer as a single "
                "JSON object matching this schema (no markdown fences, raw JSON only):\n"
                f"{schema_text}"
            )
        except Exception:
            pass

    system_msg = (
        "You are an autonomous software engineering agent. "
        "Use the provided tools (read_file, write_file, bash) to explore the repository, "
        "make changes, and run commands. "
        "Work iteratively: read files before editing them, verify changes with bash. "
        "When all work is complete, output your final result as a JSON object."
        + schema_instruction
    )

    messages: list[dict] = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt},
    ]

    total_in = 0
    total_out = 0
    is_error = False
    final_text = ""
    max_turns = 40

    for turn in range(max_turns):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            is_error = True
            final_text = str(e)
            print(f"grok API error (turn {turn}): {e}", file=sys.stderr, flush=True)
            break

        usage = resp.usage
        if usage:
            total_in += usage.prompt_tokens or 0
            total_out += usage.completion_tokens or 0

        choice = resp.choices[0]
        msg = choice.message
        messages.append(msg.model_dump(exclude_unset=True))

        if choice.finish_reason == "tool_calls" and msg.tool_calls:
            tool_results = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result_str = _dispatch_tool(tc.function.name, args, repo_root)
                print(
                    f"  tool={tc.function.name} args={str(args)[:80]} -> {result_str[:60]}",
                    flush=True,
                )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
            messages.extend(tool_results)
            continue

        # finish_reason == "stop" (or unexpected) — extract final answer
        final_text = msg.content or ""
        print(f"grok done: turn={turn} in={total_in} out={total_out} chars={len(final_text)}", flush=True)
        break
    else:
        is_error = True
        final_text = f"Exceeded max turns ({max_turns})"

    # Parse structured result
    structured = _extract_json(final_text)
    summary = str(structured.get("summary", final_text))[:2000]

    tokens_used = (total_in + total_out) or None
    price = _PRICING.get(model)
    tokens_cost_usd: float | None = None
    if price and total_in + total_out > 0:
        tokens_cost_usd = (total_in * price[0] + total_out * price[1]) / 1_000_000

    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "status": "FAILED" if is_error else "COMPLETED",
        "summary": summary,
        "completed_items": structured.get("completed_items", [summary] if not is_error else []),
        "remaining_items": structured.get("remaining_items", []),
        "decisions": structured.get("decisions", []),
        "discoveries": structured.get("discoveries", []),
        "blockers": structured.get("blockers", []),
        "validation": structured.get("validation", []),
        "verdict": _infer_verdict(structured, summary),
        "requirements_assessment": structured.get("requirements_assessment", []),
        "system_fit_assessment": structured.get("system_fit_assessment", {}),
        "capability_mutation": structured.get("capability_mutation", None),
        "files_changed": structured.get("files_changed", []),
        "warnings": structured.get("warnings", []),
        "artifacts": structured.get("artifacts", []),
        "tokens_used": tokens_used,
        "tokens_cost_usd": tokens_cost_usd,
        "grok_raw": {"model": model, "usage_in": total_in, "usage_out": total_out},
    }

    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "summary_preview": summary[:200]}))
    sys.exit(1 if is_error else 0)


if __name__ == "__main__":
    main()
