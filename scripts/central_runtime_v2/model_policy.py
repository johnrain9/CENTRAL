"""Model-selection policy helpers for central_runtime_v2.

All functions are pure / no I/O (env reads aside).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from central_runtime_v2.config import (
    ALLOWED_REASONING_EFFORTS,
    AUTONOMY_PROFILE,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CLAUDE_MODEL_ENV,
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_MODEL_ENV,
    DEFAULT_WORKER_MODEL_ENV,
    HIGH_TIER_CLAUDE_MODEL,
    HIGH_TIER_CODEX_MODEL,
    HIGH_TIER_TAGS,
    MEDIUM_TIER_CLAUDE_MODEL,
    MEDIUM_TIER_CODEX_MODEL,
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


def resolve_worker_codex_model(
    snapshot: dict[str, Any], dispatcher_default_codex_model: str
) -> ModelSelection:
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    task_override = normalize_optional_string(execution_metadata.get("codex_model"))
    if task_override is not None:
        return ModelSelection(value=task_override, source="task_override")
    task_class = resolve_task_class(snapshot)
    policy_model, policy_source = resolve_policy_model(task_class, "codex")
    # Only apply policy if it differs from the medium-tier default; otherwise fall
    # through to dispatcher_default so operator-configured defaults still apply.
    if task_class == "design":
        return ModelSelection(value=policy_model, source=policy_source)
    return ModelSelection(
        value=normalize_codex_model(
            dispatcher_default_codex_model, label="dispatcher default codex model"
        ),
        source="dispatcher_default",
    )


def resolve_default_claude_model(explicit: str | None) -> str:
    if explicit is not None:
        return normalize_codex_model(explicit, label="default claude model")
    env_value = normalize_optional_string(os.environ.get(DEFAULT_CLAUDE_MODEL_ENV))
    if env_value is not None:
        return env_value
    return DEFAULT_CLAUDE_MODEL


def resolve_worker_claude_model(
    snapshot: dict[str, Any], dispatcher_default_claude_model: str
) -> ModelSelection:
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    task_override = normalize_optional_string(execution_metadata.get("claude_model"))
    if task_override is not None:
        return ModelSelection(value=task_override, source="task_override")
    task_class = resolve_task_class(snapshot)
    if task_class == "design":
        policy_model, policy_source = resolve_policy_model(task_class, "claude")
        return ModelSelection(value=policy_model, source=policy_source)
    return ModelSelection(
        value=normalize_codex_model(
            dispatcher_default_claude_model, label="dispatcher default claude model"
        ),
        source="dispatcher_default",
    )


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
        model = HIGH_TIER_CLAUDE_MODEL if backend == "claude" else HIGH_TIER_CODEX_MODEL
    else:
        model = MEDIUM_TIER_CLAUDE_MODEL if backend == "claude" else MEDIUM_TIER_CODEX_MODEL
    return model, "policy_default"


def resolve_default_worker_model(worker_mode: str, explicit: str | None) -> str:
    """Resolve the default model for whatever backend is configured."""
    generic_env = normalize_optional_string(os.environ.get(DEFAULT_WORKER_MODEL_ENV))
    if explicit is not None:
        return explicit
    if generic_env is not None:
        return generic_env
    if worker_mode == "claude":
        return resolve_default_claude_model(None)
    return resolve_default_codex_model(None)


def resolve_task_worker_backend(snapshot: dict[str, Any], dispatcher_default: str) -> str:
    """Allow per-task backend override via execution.metadata.worker_backend."""
    execution = snapshot.get("execution") or {}
    execution_metadata = execution.get("metadata") or {}
    override = normalize_optional_string(execution_metadata.get("worker_backend"))
    if override is not None and override in ("codex", "claude", "stub"):
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
    if effective_backend == "claude":
        claude_default = dispatcher_default_worker_model or resolve_default_claude_model(None)
        worker_model = resolve_worker_claude_model(snapshot, claude_default)
    else:
        codex_model = resolve_worker_codex_model(snapshot, dispatcher_default_codex_model)
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
    prompt_sections += [
        f"## Objective\n{snapshot.get('objective_md', '').strip()}",
        f"## Context\n{snapshot.get('context_md', '').strip()}",
        f"## Scope\n{snapshot.get('scope_md', '').strip()}",
        f"## Deliverables\n{snapshot.get('deliverables_md', '').strip()}",
        f"## Acceptance\n{snapshot.get('acceptance_md', '').strip()}",
        f"## Testing\n{snapshot.get('testing_md', '').strip()}",
        f"## Dispatch Contract\n{snapshot.get('dispatch_md', '').strip()}",
        f"## Closeout Contract\n{snapshot.get('closeout_md', '').strip()}",
        f"## Reconciliation\n{snapshot.get('reconciliation_md', '').strip()}",
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
    # Add backend-specific model fields
    if effective_backend == "claude":
        result["worker_model"] = worker_model.value
        result["worker_model_source"] = worker_model.source
    elif effective_backend == "codex":
        result["codex_profile"] = execution_metadata.get("codex_profile") or AUTONOMY_PROFILE
        result["codex_model"] = codex_model.value
        result["codex_model_source"] = codex_model.source
        raw_effort = normalize_optional_string(execution_metadata.get("codex_effort"))
        result["codex_effort"] = raw_effort if raw_effort in ALLOWED_REASONING_EFFORTS else DEFAULT_CODEX_EFFORT
        # Generic aliases for codex
        result["worker_model"] = codex_model.value
        result["worker_model_source"] = codex_model.source
    else:
        result["worker_model"] = None
        result["worker_model_source"] = None
    return result
