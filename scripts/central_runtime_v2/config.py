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

DEFAULT_CODEX_MODEL = "gpt-5-codex"
DEFAULT_CODEX_MODEL_ENV = "CENTRAL_DISPATCHER_CODEX_MODEL"

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_CLAUDE_MODEL_ENV = "CENTRAL_DISPATCHER_CLAUDE_MODEL"

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
MEDIUM_TIER_CLAUDE_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL
)
MEDIUM_TIER_CODEX_MODEL: str = os.environ.get(
    "CENTRAL_DISPATCHER_MEDIUM_TIER_CODEX_MODEL", DEFAULT_CODEX_MODEL
)

# Task classes that trigger high-tier model selection.
HIGH_TIER_TAGS: frozenset[str] = frozenset({"design", "architecture", "planning", "spec"})

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
    default_worker_model: str | None = None
    default_codex_model: str = DEFAULT_CODEX_MODEL
    max_retries: int = 5
    notify: bool = False

    def __post_init__(self) -> None:
        # Unify: if only one is set, sync them
        if self.default_worker_model and not self.default_codex_model:
            object.__setattr__(self, "default_codex_model", self.default_worker_model)
        elif self.default_codex_model and not self.default_worker_model:
            object.__setattr__(self, "default_worker_model", self.default_codex_model)
        elif not self.default_worker_model and not self.default_codex_model:
            object.__setattr__(self, "default_worker_model", DEFAULT_CODEX_MODEL)
            object.__setattr__(self, "default_codex_model", DEFAULT_CODEX_MODEL)


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
