"""Constants, dataclasses, and small helpers for central_runtime_v2.

Stdlib-only imports — no internal V2 sibling imports.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
# config.py lives at scripts/central_runtime_v2/config.py
# → parent = central_runtime_v2/   → parent.parent = scripts/   → parent.parent.parent = repo root
REPO_ROOT = SCRIPT_PATH.parent.parent.parent
DEFAULT_STATE_DIR = REPO_ROOT / "state" / "central_runtime"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "central_tasks.db"

# Override via CENTRAL_AUTONOMY_ROOT; default: ../Dispatcher
AUTONOMY_ROOT = Path(
    os.environ.get("CENTRAL_AUTONOMY_ROOT", str(REPO_ROOT.parent / "Dispatcher"))
)
AUTONOMY_SCHEMA_PATH = AUTONOMY_ROOT / "autonomy" / "schemas" / "worker_result.schema.json"
AUTONOMY_PROFILE: str | None = os.environ.get("AUTONOMY_PROFILE")

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
DEFAULT_CODEX_MODEL_ENV = "CENTRAL_DISPATCHER_CODEX_MODEL"

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_CLAUDE_MODEL_ENV = "CENTRAL_DISPATCHER_CLAUDE_MODEL"

DEFAULT_GEMINI_MODEL = "gemini-3-pro-preview"
DEFAULT_GEMINI_MODEL_ENV = "CENTRAL_DISPATCHER_GEMINI_MODEL"

DEFAULT_GROK_MODEL = "grok-4-1-fast-non-reasoning"
DEFAULT_GROK_MODEL_ENV = "CENTRAL_DISPATCHER_GROK_MODEL"

DEFAULT_WORKER_MODEL_ENV = "CENTRAL_DISPATCHER_WORKER_MODEL"

# Model policy tiers.
# High tier is used for design/architecture tasks; medium is the routine default.
# These can be overridden via environment variables.
HIGH_TIER_CLAUDE_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_HIGH_TIER_CLAUDE_MODEL", "claude-opus-4-6"
)
HIGH_TIER_CODEX_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_HIGH_TIER_CODEX_MODEL", "o3"
)
HIGH_TIER_GEMINI_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_HIGH_TIER_GEMINI_MODEL", "gemini-3-pro-preview"
)
HIGH_TIER_GROK_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_HIGH_TIER_GROK_MODEL", "grok-4.20-0309-reasoning"
)
MEDIUM_TIER_CLAUDE_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL
)
MEDIUM_TIER_CODEX_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_CODEX_MODEL", DEFAULT_CODEX_MODEL
)
MEDIUM_TIER_GEMINI_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_GEMINI_MODEL", "gemini-3-flash-preview"
)
MEDIUM_TIER_GROK_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_GROK_MODEL", DEFAULT_GROK_MODEL
)

# Task classes that trigger high-tier model selection.
HIGH_TIER_TAGS: frozenset[str] = frozenset({"design", "architecture", "planning", "spec"})

# Allowed codex worker models (operator-facing allowlist).
ALLOWED_CODEX_MODELS: frozenset[str] = frozenset({"gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"})

# Allowed grok worker models.
ALLOWED_GROK_MODELS: frozenset[str] = frozenset({
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-4-1-fast-reasoning",
    "grok-4-1-fast-non-reasoning",
    "grok-3-beta",
    "grok-3-mini-beta",
})

# Allowed gemini worker models.
ALLOWED_GEMINI_MODELS: frozenset[str] = frozenset({
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
})

# Reasoning effort levels (shared by codex and claude backends).
ALLOWED_REASONING_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "max"})
DEFAULT_WORKER_EFFORT = "medium"
# Backend-specific aliases (kept for any legacy references).
DEFAULT_CODEX_EFFORT = DEFAULT_WORKER_EFFORT
DEFAULT_CLAUDE_EFFORT = DEFAULT_WORKER_EFFORT

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimePaths:
    state_dir: Path
    lock_path: Path
    log_path: Path
    worker_status_cache_path: Path
    worker_logs_dir: Path
    worker_results_dir: Path
    worker_prompts_dir: Path


@dataclass
class ActiveWorker:
    task: dict[str, Any]
    worker_id: str
    run_id: str
    pid: int
    proc: subprocess.Popen[str] | None
    log_handle: Any | None
    prompt_path: Path
    result_path: Path
    log_path: Path
    process_start_token: str | None
    started_at: datetime | None
    start_monotonic: float | None
    last_heartbeat_monotonic: float
    timeout_seconds: int
    adopted: bool = False
    selected_worker_model: str | None = None
    selected_worker_model_source: str | None = None
    selected_worker_backend: str | None = None
    pgid: int | None = None
    is_remote: bool = False
    remote_worker_id: str | None = None
    last_remote_heartbeat: str | None = None


@dataclass
class DispatcherConfig:
    db_path: Path
    state_dir: Path
    max_workers: int
    poll_interval: float
    heartbeat_seconds: float
    status_heartbeat_seconds: float
    stale_recovery_seconds: float
    worker_mode: str
    default_worker_model: str
    max_retries: int = 5
    notify: bool = False
    audit_worker_model: str | None = None


@dataclass(frozen=True)
class ModelSelection:
    value: str
    source: str


# Backward-compatible alias
CodexModelSelection = ModelSelection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def snapshot_retry_count(snapshot: dict[str, Any]) -> int:
    """Return the current retry_count from a claimed task snapshot, or 0 if absent."""
    runtime = snapshot.get("runtime") or {}
    return int(runtime.get("retry_count") or 0)
