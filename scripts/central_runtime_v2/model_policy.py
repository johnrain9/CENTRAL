"""Model-selection policy helpers for central_runtime_v2.

All functions are pure / no I/O (env reads aside).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from central_runtime_v2.config import (
    ALLOWED_REASONING_EFFORTS,
    AUTONOMY_PROFILE,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CLAUDE_MODEL_ENV,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_MODEL_ENV,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_MODEL_ENV,
    DEFAULT_GROK_MODEL,
    DEFAULT_GROK_MODEL_ENV,
    DEFAULT_WORKER_EFFORT,
    DEFAULT_WORKER_MODEL_ENV,
    HIGH_TIER_CLAUDE_MODEL,
    HIGH_TIER_CODEX_MODEL,
    HIGH_TIER_GEMINI_MODEL,
    HIGH_TIER_GROK_MODEL,
    HIGH_TIER_TAGS,
    MEDIUM_TIER_CLAUDE_MODEL,
    MEDIUM_TIER_CODEX_MODEL,
    MEDIUM_TIER_GEMINI_MODEL,
    MEDIUM_TIER_GROK_MODEL,
    ModelSelection,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def extract_markdown_items(text: str) -> list[str]:
    items: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line[:2].isdigit() and ". " in line:
            items.append(line.split(". ", 1)[1].strip())
    return [item for item in items if item]


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_codex_model(value: Any, *, label: str = "model") -> str:
    text = normalize_optional_string(value)
    if text is None:
        _die(f"{label} must be a non-empty string")
    return text  # type: ignore[return-value]


def resolve_default_codex_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default codex model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_CODEX_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_CODEX_MODEL


def resolve_worker_model(
    snapshot: dict[str, Any], dispatcher_default: str, backend: str
) -> ModelSelection:
    """Unified model resolver for all backends (codex, claude, gemini).

    Resolution priority:
    1. execution.metadata.worker_model  — unified override (any backend)
    2. execution.metadata.{backend}_model  — backend-specific override
    3. Policy: design tasks get the high-tier model for this backend
    4. dispatcher_default
    """
    execution_metadata = (snapshot.get("execution") or {}).get("metadata") or {}
    unified_override = normalize_optional_string(execution_metadata.get("worker_model"))
    if unified_override is not None:
        return ModelSelection(value=unified_override, source="task_override")
    specific_override = normalize_optional_string(execution_metadata.get(f"{backend}_model"))
    if specific_override is not None:
        return ModelSelection(value=specific_override, source="task_override")
    task_class = resolve_task_class(snapshot)
    if task_class == "design":
        policy_model, policy_source = resolve_policy_model(task_class, backend)
        return ModelSelection(value=policy_model, source=policy_source)
    return ModelSelection(
        value=normalize_codex_model(dispatcher_default, label=f"dispatcher default {backend} model"),
        source="dispatcher_default",
    )


def resolve_default_claude_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default claude model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_CLAUDE_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_CLAUDE_MODEL


def resolve_task_class(snapshot: dict[str, Any]) -> str:
    """Return the task class ('design' or 'routine') for model policy selection.

    Detection priority:
    1. execution.metadata.task_class explicit override
    2. metadata.tags contains a high-tier tag (design, architecture, planning, spec)
    3. metadata.phase contains 'design' or 'architecture'
    4. Default: 'routine'
    """
    execution_metadata = (snapshot.get("execution") or {}).get("metadata") or {}
    explicit_class = normalize_optional_string(execution_metadata.get("task_class"))
    if explicit_class is not None:
        return explicit_class.lower()
    metadata = snapshot.get("metadata") or {}
    tags = {str(t).lower() for t in (metadata.get("tags") or [])}
    if tags & HIGH_TIER_TAGS:
        return "design"
    phase = normalize_optional_string(metadata.get("phase")) or ""
    if any(kw in phase.lower() for kw in ("design", "architecture", "planning", "spec")):
        return "design"
    return "routine"


def resolve_policy_model(task_class: str, backend: str) -> tuple[str, str]:
    """Return (model, source) for the given task_class and backend.

    Returns the high-tier model for 'design' tasks, medium-tier for everything else.
    Source tag is 'policy_default' so callers can inspect where the model came from.
    """
    if task_class == "design":
        if backend == "claude":
            model = HIGH_TIER_CLAUDE_MODEL
        elif backend == "gemini":
            model = HIGH_TIER_GEMINI_MODEL
        elif backend == "grok":
            model = HIGH_TIER_GROK_MODEL
        else:
            model = HIGH_TIER_CODEX_MODEL
    else:
        if backend == "claude":
            model = MEDIUM_TIER_CLAUDE_MODEL
        elif backend == "gemini":
            model = MEDIUM_TIER_GEMINI_MODEL
        elif backend == "grok":
            model = MEDIUM_TIER_GROK_MODEL
        else:
            model = MEDIUM_TIER_CODEX_MODEL
    return model, "policy_default"


def resolve_default_gemini_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default gemini model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_GEMINI_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_GEMINI_MODEL


def resolve_default_grok_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default grok model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_GROK_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_GROK_MODEL


def resolve_default_worker_model(worker_mode: str, explicit: str | None) -> str:
    """Resolve the default model for whatever backend is configured."""
    generic_env = normalize_optional_string(os.environ.get(DEFAULT_WORKER_MODEL_ENV))
    if explicit is not None:
        return explicit
    if generic_env is not None:
        return generic_env
    if worker_mode == "claude":
        return resolve_default_claude_model(None)
    if worker_mode == "gemini":
        return resolve_default_gemini_model(None)
    if worker_mode == "grok":
        return resolve_default_grok_model(None)
    return resolve_default_codex_model(None)


def resolve_task_worker_backend(snapshot: dict[str, Any], dispatcher_default: str) -> str:
    """Allow per-task backend override via execution.metadata.worker_backend."""
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    override = normalize_optional_string(execution_metadata.get("worker_backend"))
    if override is not None and override in ("codex", "claude", "gemini", "grok", "stub"):
        return override
    return dispatcher_default


def build_worker_task(
    snapshot: dict[str, Any],
    dispatcher_default_codex_model: str,
    *,
    worker_mode: str = "codex",
    dispatcher_default_worker_model: str | None = None,
) -> dict[str, Any]:
    execution = snapshot.get("execution") or {}
    metadata = snapshot.get("metadata") or {}
    execution_metadata = execution.get("metadata") or {}
    effective_backend = resolve_task_worker_backend(snapshot, worker_mode)
    # When a task overrides to a different backend, use that backend's default
    # model — not the dispatcher's current default (which is for a different backend).
    _backend_defaults = {
        "claude": resolve_default_claude_model(None),
        "gemini": resolve_default_gemini_model(None),
        "grok": resolve_default_grok_model(None),
    }
    if effective_backend != worker_mode and effective_backend in _backend_defaults:
        _backend_default = _backend_defaults[effective_backend]
    else:
        _backend_default = dispatcher_default_worker_model or _backend_defaults.get(
            effective_backend, dispatcher_default_codex_model
        )
    resolved_model = resolve_worker_model(snapshot, _backend_default, effective_backend)
    deliverables = extract_markdown_items(snapshot.get("deliverables_md", "")) or [
        snapshot.get("deliverables_md", "").strip()
    ]
    scope_notes = extract_markdown_items(snapshot.get("scope_md", "")) or [
        snapshot.get("scope_md", "").strip()
    ]
    validation_commands = extract_markdown_items(snapshot.get("testing_md", "")) or [
        snapshot.get("testing_md", "").strip()
    ]
    validation_commands = [item for item in validation_commands if item]
    deliverables = [item for item in deliverables if item]
    scope_notes = [item for item in scope_notes if item]
    rework_context = str(metadata.get("rework_context") or "").strip()
    rework_count = int(metadata.get("rework_count") or 0)
    prompt_sections = []
    if rework_context:
        prompt_sections.append(
            f"## REWORK (attempt {rework_count})\n"
            f"A previous attempt failed audit. Fix **only** the specific issues listed below.\n"
            f"Do not explore unrelated code or documents. Make targeted changes only.\n\n"
            f"{rework_context}"
        )
    _repo_root = str(snapshot.get("target_repo_root") or "")
    _is_ecosystem = Path(_repo_root).name == "ecosystem"
    _task_kind = str(execution.get("task_kind") or "mutating").strip().lower()
    _is_read_only = _task_kind == "read_only"
    if _is_read_only:
        _completion_gates = (
            "## Completion Gates (Mandatory)\n"
            "Before reporting done, you MUST complete and verify all of the following:\n"
            "- Write a structured findings report in the closeout summary and include a passing "
            "validation entry named `report written`.\n"
            "- Do NOT run cargo build or make code commits — this is a read-only task.\n"
            "- Do not mark task done until the report is written and you can prove it via a "
            "validation entry.\n"
            "- If any check fails, return status `FAILED` with notes explaining why."
        )
    else:
        _completion_gates = (
            "## Completion Gates (Mandatory)\n"
            "Before reporting done, you MUST complete and verify all of the following:\n"
            "- Run `cargo build` and include a passing validation entry named `cargo build`.\n"
            "- Commit all repo changes and include a passing validation entry named `git commit`.\n"
        )
        if _is_ecosystem:
            _completion_gates += (
                "- Run `cd frontend && npx vitest run --project unit` and include a passing "
                "validation entry named `frontend unit tests`.\n"
                "- Run `cargo test --lib` and include a passing validation entry named `cargo test lib`.\n"
            )
        _completion_gates += (
            "- Do not mark task done until all checks have run successfully and you can prove it "
            "via validation entries.\n"
            "- If any check fails, return status `FAILED` with notes explaining why."
        )
    prompt_sections += [
        f"## Objective\n{snapshot.get('objective_md', '').strip()}",
        f"## Context\n{snapshot.get('context_md', '').strip()}",
        f"## Scope\n{snapshot.get('scope_md', '').strip()}",
        f"## Deliverables\n{snapshot.get('deliverables_md', '').strip()}",
        f"## Acceptance\n{snapshot.get('acceptance_md', '').strip()}",
        f"## Testing\n{snapshot.get('testing_md', '').strip()}",
        _completion_gates,
        f"## Dispatch Contract\n{snapshot.get('dispatch_md', '').strip()}",
        f"## Closeout Contract\n{snapshot.get('closeout_md', '').strip()}",
        f"## Reconciliation\n{snapshot.get('reconciliation_md', '').strip()}",
        (
            "## Output Contract\n"
            "Return valid JSON matching the worker result schema. Field guidance:\n"
            "- status: COMPLETED, PARTIAL, BLOCKED, or FAILED\n"
            "- summary: Brief prose narrative of what was done and the outcome\n"
            "- decisions: Array of strings. Each significant decision made or confirmed during "
            "the work — e.g. 'Chose X over Y because Z', 'Accepted implementation as-is "
            "because all checks passed'. Populate with at least one entry whenever you made or "
            "reviewed a decision. Do NOT return an empty array if decisions were made.\n"
            "- discoveries: Array of strings. Each non-obvious thing found during the work that "
            "was not in the original spec — e.g. 'Found that X also affects Y', 'Noticed "
            "pre-existing issue with Z unrelated to this task'. Populate whenever something "
            "worth surfacing was found. Do NOT return an empty array if discoveries were made.\n"
            "- warnings: Array of strings. Each risk, concern, or issue noted even if not "
            "blocking — e.g. 'Pre-existing unrelated modifications in worktree', 'Edge case "
            "not covered by tests'. Populate whenever anything raised a flag. Do NOT return "
            "an empty array if warnings apply.\n"
            "- completed_items / remaining_items: Checklist items done vs still pending\n"
            "- validation: Array of {name, passed, notes} for each check run\n"
            "- files_changed: Paths of files modified\n"
            "- verdict, requirements_assessment, system_fit_assessment, blockers, artifacts: "
            "as applicable to this task type\n"
            "IMPORTANT: decisions, discoveries, and warnings are machine-read for analytics. "
            "Always populate them with substantive content — never leave all three empty."
        ),
    ]
    task_category = snapshot.get("task_type") or "implementation"
    if task_category not in {"implementation", "truth"}:
        task_category = "infrastructure"
    result = {
        "id": snapshot["task_id"],
        "title": snapshot["title"],
        "category": task_category,
        "task_kind": execution.get("task_kind") or "mutating",
        "repo_root": snapshot["target_repo_root"],
        "prompt_body": "\n\n".join(section for section in prompt_sections if section.strip()),
        "deliverables_json": json.dumps(deliverables),
        "scope_notes_json": json.dumps(scope_notes),
        "validation_commands_json": json.dumps(validation_commands),
        "design_doc_path": metadata.get("design_doc_path"),
        "worker_backend": effective_backend,
        "sandbox_mode": execution.get("sandbox_mode"),
        "approval_policy": execution.get("approval_policy"),
        "additional_writable_dirs_json": json.dumps(
            execution.get("additional_writable_dirs") or []
        ),
    }
    # Resolve effort: worker_effort is canonical; fall back to backend-specific keys for
    # backward compat with tasks patched before the unification.
    _raw_effort = (
        normalize_optional_string(execution_metadata.get("worker_effort"))
        or normalize_optional_string(execution_metadata.get("claude_effort"))
        or normalize_optional_string(execution_metadata.get("codex_effort"))
    )
    _effort = _raw_effort if _raw_effort in ALLOWED_REASONING_EFFORTS else None

    result["worker_model"] = resolved_model.value
    result["worker_model_source"] = resolved_model.source

    if effective_backend == "codex":
        result["codex_profile"] = execution_metadata.get("codex_profile") or AUTONOMY_PROFILE
        result["codex_model"] = resolved_model.value
        result["codex_model_source"] = resolved_model.source
        _spark_default = "high" if resolved_model.value == "gpt-5.3-codex-spark" else DEFAULT_WORKER_EFFORT
        result["codex_effort"] = _effort if _effort else _spark_default
    elif effective_backend == "grok":
        # Grok uses the codex CLI pointed at api.x.ai/v1 — needs the same codex_ fields
        result["codex_model"] = resolved_model.value
        result["codex_model_source"] = resolved_model.source
        result["codex_effort"] = _effort if _effort else DEFAULT_WORKER_EFFORT
    elif effective_backend == "claude":
        result["worker_effort"] = _effort if _effort else DEFAULT_WORKER_EFFORT
    elif effective_backend not in ("gemini", "stub"):
        pass  # unknown backend — model fields already set above
    return result
