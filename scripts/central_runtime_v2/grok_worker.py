"""Grok worker subprocess script.

Called by the dispatcher via `python3 -m central_runtime_v2.grok_worker <args>`.
Reads prompt from stdin, calls xAI API via OpenAI SDK, writes worker_result JSON.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 5:
        print("Usage: grok_worker.py <model> <task_id> <run_id> <result_path> [schema_path]", file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    task_id = sys.argv[2]
    run_id = sys.argv[3]
    result_path = Path(sys.argv[4])
    schema_path = Path(sys.argv[5]) if len(sys.argv) > 5 else None

    prompt = sys.stdin.read()

    api_key = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY", "")
    if not api_key:
        print("ERROR: GROK_API_KEY or XAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # Build system instruction with schema if available
    system_content = ""
    if schema_path and schema_path.exists():
        try:
            schema_text = schema_path.read_text(encoding="utf-8").strip()
            json.loads(schema_text)  # validate
            system_content = (
                "You MUST return your final answer as a single JSON object matching this schema:\n"
                f"{schema_text}\n"
                "Return ONLY the JSON object, no markdown fences or extra text."
            )
        except Exception:
            pass

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    messages: list[dict[str, str]] = []
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.append({"role": "user", "content": prompt})

    is_error = False
    response_text = ""
    usage_in = 0
    usage_out = 0

    try:
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.3)
        response_text = resp.choices[0].message.content or ""
        usage_in = resp.usage.prompt_tokens if resp.usage else 0
        usage_out = resp.usage.completion_tokens if resp.usage else 0
        print(f"grok response: {len(response_text)} chars, {usage_in}+{usage_out} tokens", flush=True)
    except Exception as e:
        is_error = True
        response_text = str(e)
        print(f"grok error: {e}", file=sys.stderr, flush=True)

    # Extract structured JSON — strip markdown code fences if present
    structured: dict = {}
    json_text = response_text
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", json_text)
    if fence_match:
        json_text = fence_match.group(1)
    try:
        parsed = json.loads(json_text)
        if isinstance(parsed, dict) and "schema_version" in parsed:
            structured = parsed
    except Exception:
        pass

    summary = str(structured.get("summary", response_text))[:2000]
    sl = summary.lower()

    # Verdict inference
    verdict = structured.get("verdict") or None
    if not verdict:
        verdict = "accepted"
        if "rework_required" in sl:
            verdict = "rework_required"
        else:
            vi = sl.find("verdict")
            if vi >= 0 and ("fail" in sl[vi : vi + 80] or "\u274c" in summary[vi : vi + 80]):
                verdict = "rework_required"

    tokens_used = (usage_in + usage_out) or None

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
        "verdict": verdict,
        "requirements_assessment": structured.get("requirements_assessment", []),
        "system_fit_assessment": structured.get("system_fit_assessment", {}),
        "capability_mutation": structured.get("capability_mutation", None),
        "files_changed": structured.get("files_changed", []),
        "warnings": structured.get("warnings", []),
        "artifacts": structured.get("artifacts", []),
        "tokens_used": tokens_used,
        "grok_raw": {"model": model, "usage_in": usage_in, "usage_out": usage_out},
    }

    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "summary_preview": summary[:200]}))
    sys.exit(1 if is_error else 0)


if __name__ == "__main__":
    main()
