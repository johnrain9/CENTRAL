#!/usr/bin/env python3
"""Seed the capability registry from curated CENTRAL task history."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
TASK_DB_SCRIPT = REPO_ROOT / "scripts" / "central_task_db.py"

BOOTSTRAP_REASON = (
    "Phase 0 capability bootstrap from done CENTRAL task history for day-one overlap coverage."
)

CAPABILITY_SPECS: list[dict[str, Any]] = [
    {
        "capability_id": "dispatcher_parked_task_visibility",
        "source_task_id": "CENTRAL-OPS-28",
        "name": "Dispatcher parked task visibility",
        "summary": "Dispatcher status surfaces parked non-eligible tasks and why they are parked.",
        "kind": "reporting_surface",
        "scope_kind": "local",
        "when_to_use_md": "Use when triaging queue state or validating why work is not dispatchable yet.",
        "do_not_use_for_md": "Do not treat the parked list as a reservation or scheduling commitment.",
        "entrypoints": ["python3 scripts/dispatcher_control.py status"],
        "keywords": ["dispatcher", "parked", "status", "eligibility"],
    },
    {
        "capability_id": "dispatcher_claim_time_audit_priority",
        "source_task_id": "CENTRAL-OPS-29",
        "name": "Dispatcher claim-time audit priority",
        "summary": "Dispatcher re-evaluates eligibility at claim time and prefers ready audit tasks over ordinary implementation work.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when reasoning about task claim order, audit responsiveness, or dispatcher fairness.",
        "do_not_use_for_md": "Do not assume the heartbeat next-claim hint means work is precommitted.",
        "entrypoints": ["python3 scripts/dispatcher_control.py status"],
        "keywords": ["dispatcher", "audit", "priority", "claim-time"],
    },
    {
        "capability_id": "dispatcher_worker_log_observability",
        "source_task_id": "CENTRAL-OPS-30",
        "name": "Dispatcher worker log observability",
        "summary": "Dispatcher worker-status reports log size, recent growth, staleness, and signal classification for active workers.",
        "kind": "reporting_surface",
        "scope_kind": "local",
        "when_to_use_md": "Use when diagnosing a running worker without opening the raw log first.",
        "do_not_use_for_md": "Do not treat log growth alone as proof of healthy task progress.",
        "entrypoints": ["python3 scripts/dispatcher_control.py worker-status"],
        "keywords": ["dispatcher", "worker-status", "logs", "stale"],
    },
    {
        "capability_id": "operator_kill_task_failure_control",
        "source_task_id": "CENTRAL-OPS-31",
        "name": "Operator kill-task failure control",
        "summary": "Operators can stop an in-flight worker and force the task into a failed runtime outcome.",
        "kind": "operator_tool",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when a worker must be interrupted immediately and the task should be failed rather than allowed to continue.",
        "do_not_use_for_md": "Do not use as a normal completion path for work that can finish cleanly.",
        "entrypoints": ["python3 scripts/dispatcher_control.py kill-task"],
        "keywords": ["operator", "kill-task", "worker", "fail"],
    },
    {
        "capability_id": "planner_queue_triage_panel",
        "source_task_id": "CENTRAL-OPS-33",
        "name": "Planner queue triage panel",
        "summary": "Planner CLI provides a triage panel covering eligible work, parked work, stale tasks, awaiting-audit tasks, failures, and recent deltas.",
        "kind": "planner_tool",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when a planner needs a fast portfolio triage view without opening the UI.",
        "do_not_use_for_md": "Do not treat the panel as the canonical scheduler policy implementation.",
        "entrypoints": ["python3 scripts/central_task_db.py view-planner-panel"],
        "keywords": ["planner", "triage", "panel", "queue"],
    },
    {
        "capability_id": "planner_audit_linkage_reporting",
        "source_task_id": "CENTRAL-OPS-34",
        "name": "Planner audit linkage reporting",
        "summary": "Planner reports and exports surface implementation/audit pairs, ready audits, awaiting-audit parents, verdicts, and rework linkage.",
        "kind": "reporting_surface",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when tracking audit queues, audit outcomes, or parent-child relationships between implementation and audit tasks.",
        "do_not_use_for_md": "Do not assume every done task has a paired audit; respect task audit_mode.",
        "entrypoints": [
            "python3 scripts/central_task_db.py view-audits",
            "python3 scripts/central_task_db.py view-summary",
        ],
        "keywords": ["audit", "planner", "reporting", "linkage"],
    },
    {
        "capability_id": "planner_task_creation_helper_presets",
        "source_task_id": "CENTRAL-OPS-35",
        "name": "Planner task creation helper presets",
        "summary": "The planner task helper supports presets, list-style section flags, explicit audit semantics, and graph previews for canonical task creation.",
        "kind": "planner_tool",
        "scope_kind": "local",
        "when_to_use_md": "Use when creating new canonical tasks with reduced boilerplate but full CENTRAL schema fidelity.",
        "do_not_use_for_md": "Do not bypass canonical DB validation; this helper improves authoring only.",
        "entrypoints": ["python3 scripts/create_planner_task.py --help"],
        "keywords": ["planner", "task-create", "helper", "presets"],
    },
    {
        "capability_id": "canonical_backfill_audit_workflow",
        "source_task_id": "CENTRAL-OPS-36",
        "name": "Canonical backfill audit workflow",
        "summary": "Already-landed work can be backfilled into CENTRAL in awaiting_audit with landed-change metadata and an immediately eligible paired audit.",
        "kind": "workflow",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when recovering provenance for work that landed before canonical task tracking or capability capture existed.",
        "do_not_use_for_md": "Do not use for brand-new implementation work that should start as a normal planner task.",
        "entrypoints": ["python3 scripts/create_planner_task.py --backfill"],
        "keywords": ["backfill", "audit", "awaiting_audit", "provenance"],
    },
    {
        "capability_id": "truth_task_pending_review_routing",
        "source_task_id": "CENTRAL-OPS-38",
        "name": "Truth-task pending review routing",
        "summary": "Truth tasks that finish successfully remain open and move to pending_review rather than auto-closing like normal implementation tasks.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when designing or debugging truth-task lifecycle behavior and review expectations.",
        "do_not_use_for_md": "Do not assume all successful runtime completions should reconcile planner status automatically.",
        "entrypoints": ["python3 scripts/central_task_db.py runtime-transition"],
        "keywords": ["truth-task", "pending_review", "runtime", "routing"],
    },
    {
        "capability_id": "dispatcher_restart_worker_adoption",
        "source_task_id": "CENTRAL-OPS-40",
        "name": "Dispatcher restart worker adoption",
        "summary": "Dispatcher restarts can adopt the original in-flight worker run without duplicating execution or corrupting terminal state.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when reasoning about dispatcher restarts, handoff safety, or worker continuity.",
        "do_not_use_for_md": "Do not use as justification for starting duplicate workers for the same task.",
        "entrypoints": ["python3 scripts/dispatcher_control.py start"],
        "keywords": ["dispatcher", "restart", "adoption", "handoff"],
    },
    {
        "capability_id": "dispatcher_downtime_result_recovery",
        "source_task_id": "CENTRAL-OPS-41",
        "name": "Dispatcher downtime result recovery",
        "summary": "Results written while the dispatcher is down are reconciled on restart without duplicate processing.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when validating crash recovery and persisted worker result handling.",
        "do_not_use_for_md": "Do not rely on downtime as a normal processing mode; this is recovery behavior.",
        "entrypoints": ["python3 scripts/dispatcher_control.py start"],
        "keywords": ["dispatcher", "recovery", "restart", "results"],
    },
    {
        "capability_id": "dispatcher_live_tail_queue_snapshot_format",
        "source_task_id": "CENTRAL-OPS-42",
        "name": "Dispatcher live-tail queue snapshot format",
        "summary": "Dispatcher tail output uses scan-friendly queue snapshots and clearer queue counter labels for operator monitoring.",
        "kind": "reporting_surface",
        "scope_kind": "local",
        "when_to_use_md": "Use when following live dispatcher output during incident handling or queue monitoring.",
        "do_not_use_for_md": "Do not read renamed queue counters as worker failures unless the line explicitly says so.",
        "entrypoints": ["python3 scripts/dispatcher_control.py tail"],
        "keywords": ["dispatcher", "tail", "logs", "queue"],
    },
    {
        "capability_id": "dispatcher_auto_reconcile_skip_after_awaiting_audit",
        "source_task_id": "CENTRAL-OPS-43",
        "name": "Dispatcher auto-reconcile skip after awaiting audit",
        "summary": "Dispatcher suppresses redundant auto-reconcile attempts once runtime success already advanced a task to awaiting_audit.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when diagnosing auto-reconcile transitions and why a success path emitted a skip breadcrumb instead of another reconcile call.",
        "do_not_use_for_md": "Do not treat the skip breadcrumb as a failure; it indicates the transition was already applied.",
        "entrypoints": ["python3 scripts/dispatcher_control.py tail"],
        "keywords": ["dispatcher", "auto-reconcile", "awaiting_audit", "breadcrumb"],
    },
    {
        "capability_id": "planner_status_dashboard",
        "source_task_id": "CENTRAL-OPS-44",
        "name": "Planner status dashboard",
        "summary": "Planner UI exposes a live dashboard with portfolio summary, dispatcher settings, active workers, attention queues, awaiting audits, repo views, recent changes, and task explorer details.",
        "kind": "planner_tool",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when a planner needs a browser-based live status surface for CENTRAL operations.",
        "do_not_use_for_md": "Do not assume the UI replaces CLI automation or canonical DB state.",
        "entrypoints": ["python3 scripts/planner_ui.py"],
        "keywords": ["planner", "dashboard", "ui", "status"],
    },
    {
        "capability_id": "operator_runtime_requeue_and_fail_task_commands",
        "source_task_id": "CENTRAL-OPS-45",
        "name": "Operator runtime requeue and fail-task commands",
        "summary": "CENTRAL exposes operator/runtime subcommands for requeueing runtime work, failing tasks, and viewing planner/audit surfaces directly from the canonical CLI.",
        "kind": "operator_tool",
        "scope_kind": "local",
        "when_to_use_md": "Use when operating runtime recovery or planner triage entirely from `central_task_db.py` commands.",
        "do_not_use_for_md": "Do not assume legacy missing subcommands exist in older environments.",
        "entrypoints": [
            "python3 scripts/central_task_db.py runtime-requeue-task",
            "python3 scripts/central_task_db.py operator-fail-task",
            "python3 scripts/central_task_db.py view-planner-panel",
            "python3 scripts/central_task_db.py view-audits",
        ],
        "keywords": ["operator", "requeue", "fail-task", "cli"],
    },
    {
        "capability_id": "runtime_v2_config_contract",
        "source_task_id": "CENTRAL-OPS-46",
        "name": "Runtime V2 config contract",
        "summary": "Runtime V2 exposes stdlib-only configuration dataclasses and constants for dispatcher, paths, workers, and model selection.",
        "kind": "schema_contract",
        "scope_kind": "local",
        "when_to_use_md": "Use when wiring Runtime V2 components that need shared config or model-selection structures.",
        "do_not_use_for_md": "Do not import old runtime config modules for V2 integrations.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.config import DispatcherConfig\""],
        "keywords": ["runtime_v2", "config", "dataclasses", "model"],
    },
    {
        "capability_id": "runtime_v2_path_management",
        "source_task_id": "CENTRAL-OPS-47",
        "name": "Runtime V2 path management",
        "summary": "Runtime V2 centralizes runtime directories, locks, and filesystem path management behind the V2 paths module.",
        "kind": "runtime_behavior",
        "scope_kind": "local",
        "when_to_use_md": "Use when a V2 runtime component needs canonical state-dir, lock, or artifact path handling.",
        "do_not_use_for_md": "Do not hand-roll alternative runtime directory layouts unless the V2 path contract is intentionally being changed.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.paths import RuntimePaths\""],
        "keywords": ["runtime_v2", "paths", "locks", "state"],
    },
    {
        "capability_id": "runtime_v2_structured_daemon_logging",
        "source_task_id": "CENTRAL-OPS-48",
        "name": "Runtime V2 structured daemon logging",
        "summary": "Runtime V2 provides a DaemonLog abstraction for structured daemon-side logging.",
        "kind": "runtime_behavior",
        "scope_kind": "local",
        "when_to_use_md": "Use when a V2 runtime module needs consistent structured log emission.",
        "do_not_use_for_md": "Do not bypass the shared logger for normal daemon logging paths.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.log import DaemonLog\""],
        "keywords": ["runtime_v2", "logging", "daemon", "structured"],
    },
    {
        "capability_id": "runtime_v2_model_policy_resolution",
        "source_task_id": "CENTRAL-OPS-49",
        "name": "Runtime V2 model policy resolution",
        "summary": "Runtime V2 resolves worker model selection and prompt construction through a dedicated model policy module.",
        "kind": "runtime_behavior",
        "scope_kind": "local",
        "when_to_use_md": "Use when deriving effective worker model choices or composing runtime prompt policy.",
        "do_not_use_for_md": "Do not duplicate model normalization logic in individual runtime callers.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.model_policy import normalize_codex_model\""],
        "keywords": ["runtime_v2", "model", "policy", "prompt"],
    },
    {
        "capability_id": "runtime_v2_worker_backend_abstraction",
        "source_task_id": "CENTRAL-OPS-50",
        "name": "Runtime V2 worker backend abstraction",
        "summary": "Runtime V2 defines backend abstractions and implementations for worker execution modes behind a shared interface.",
        "kind": "runtime_behavior",
        "scope_kind": "local",
        "when_to_use_md": "Use when selecting or extending worker execution backends in Runtime V2.",
        "do_not_use_for_md": "Do not couple new runtime code directly to one backend when the abstraction should remain interchangeable.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.backends import get_worker_backend\""],
        "keywords": ["runtime_v2", "backend", "worker", "abstraction"],
    },
    {
        "capability_id": "runtime_v2_worker_observation_layer",
        "source_task_id": "CENTRAL-OPS-51",
        "name": "Runtime V2 worker observation layer",
        "summary": "Runtime V2 exposes a worker observation layer for status inspection without depending on the dispatcher module.",
        "kind": "runtime_behavior",
        "scope_kind": "local",
        "when_to_use_md": "Use when inspecting worker state from V2 tooling without importing dispatcher internals.",
        "do_not_use_for_md": "Do not reintroduce dispatcher-module coupling into observation paths.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.observation import inspect_worker_status\""],
        "keywords": ["runtime_v2", "observation", "worker", "status"],
    },
    {
        "capability_id": "runtime_v2_dispatcher_class",
        "source_task_id": "CENTRAL-OPS-52",
        "name": "Runtime V2 dispatcher class",
        "summary": "Runtime V2 provides a dedicated CentralDispatcher class with decomposed worker finalization and reconciliation helpers.",
        "kind": "runtime_behavior",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when running or extending the V2 dispatcher implementation rather than the legacy runtime path.",
        "do_not_use_for_md": "Do not import legacy central_runtime dispatcher code for V2-only work.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.dispatcher import CentralDispatcher\""],
        "keywords": ["runtime_v2", "dispatcher", "reconcile", "workers"],
    },
    {
        "capability_id": "runtime_v2_cli_command_surface",
        "source_task_id": "CENTRAL-OPS-53",
        "name": "Runtime V2 CLI command surface",
        "summary": "Runtime V2 exposes a CLI command surface and parser glue for the main runtime subcommands.",
        "kind": "operator_tool",
        "scope_kind": "local",
        "when_to_use_md": "Use when invoking Runtime V2 through its command parser or embedding the V2 CLI entrypoint.",
        "do_not_use_for_md": "Do not assume command registration lives only in legacy runtime code.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.commands import build_parser, main\""],
        "keywords": ["runtime_v2", "cli", "commands", "parser"],
    },
    {
        "capability_id": "task_create_template_and_missing_field_aggregation",
        "source_task_id": "CENTRAL-OPS-57",
        "name": "Task-create template and missing-field aggregation",
        "summary": "The canonical task-create CLI can emit a full JSON template and reports all missing required fields in one validation pass.",
        "kind": "operator_tool",
        "scope_kind": "local",
        "when_to_use_md": "Use when authoring task-create payloads manually or debugging schema validation failures.",
        "do_not_use_for_md": "Do not expect the command to infer required fields for you; it now reports them comprehensively instead.",
        "entrypoints": [
            "python3 scripts/central_task_db.py task-create --template",
            "python3 scripts/central_task_db.py task-create --input payload.json",
        ],
        "keywords": ["task-create", "template", "validation", "cli"],
    },
    {
        "capability_id": "dispatcher_single_default_worker_model_contract",
        "source_task_id": "CENTRAL-OPS-58",
        "name": "Dispatcher single default worker model contract",
        "summary": "Dispatcher configuration now uses a single required default_worker_model field instead of drift-prone dual model defaults.",
        "kind": "schema_contract",
        "scope_kind": "local",
        "when_to_use_md": "Use when reading or writing dispatcher config and when reasoning about effective worker-model defaults.",
        "do_not_use_for_md": "Do not depend on old dual-field synchronization semantics except for compatibility shims.",
        "entrypoints": ["python3 -c \"from scripts.central_runtime_v2.config import DispatcherConfig\""],
        "keywords": ["dispatcher", "config", "model", "default_worker_model"],
    },
    {
        "capability_id": "dispatcher_check_and_stub_self_test",
        "source_task_id": "CENTRAL-OPS-60",
        "name": "Dispatcher check and stub self-test",
        "summary": "Dispatcher control exposes a check command that validates config, prints effective settings, and runs a stub runtime self-check.",
        "kind": "operator_tool",
        "scope_kind": "local",
        "when_to_use_md": "Use before starting or changing dispatcher config to verify the effective runtime setup quickly.",
        "do_not_use_for_md": "Do not treat the stub self-check as a full end-to-end production readiness test.",
        "entrypoints": ["python3 scripts/dispatcher_control.py check"],
        "keywords": ["dispatcher", "check", "self-check", "config"],
    },
    {
        "capability_id": "planner_health_dashboard",
        "source_task_id": "CENTRAL-OPS-61",
        "name": "Planner health dashboard",
        "summary": "Planner UI supports a health-first dashboard with repo cards, initiative progress bars, conditional attention rendering, and task explorer retention.",
        "kind": "planner_tool",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when planners need a repo-centric health view instead of a queue-centric status surface.",
        "do_not_use_for_md": "Do not expect sections with zero items to remain expanded in this dashboard layout.",
        "entrypoints": ["python3 scripts/planner_ui.py"],
        "keywords": ["planner", "dashboard", "health", "repo-cards"],
    },
    {
        "capability_id": "repo_health_snapshot_collection",
        "source_task_id": "CENTRAL-OPS-62",
        "name": "Repo health snapshot collection",
        "summary": "CENTRAL collects repo-health snapshots after successful work, including bounded test runs, coverage metadata, and latest-report retrieval for dashboards.",
        "kind": "reporting_surface",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when surfacing post-task repo health, test counts, or coverage freshness in planner/operator workflows.",
        "do_not_use_for_md": "Do not assume every repo snapshot implies a fully passing test suite; snapshot metadata records failures too.",
        "entrypoints": [
            "python3 scripts/repo_health_check.py $PROJECTS_DIR/CENTRAL",
            "python3 scripts/central_task_db.py health-snapshot-latest --repo CENTRAL",
        ],
        "keywords": ["repo-health", "snapshot", "tests", "coverage"],
    },
    {
        "capability_id": "capability_registry_core_crud",
        "source_task_id": "CENTRAL-OPS-65",
        "name": "Capability registry core CRUD",
        "summary": "CENTRAL stores canonical capability rows in the SQLite task DB and exposes list/show/create CLI support for registry management.",
        "kind": "operator_tool",
        "scope_kind": "workflow",
        "when_to_use_md": "Use when querying or seeding the canonical capability registry in CENTRAL.",
        "do_not_use_for_md": "Do not assume mutation paths beyond create/list/show are implemented in this initial registry foundation.",
        "entrypoints": [
            "python3 scripts/central_task_db.py capability-list",
            "python3 scripts/central_task_db.py capability-show --capability-id <id>",
            "python3 scripts/central_task_db.py capability-create --input payload.json",
        ],
        "keywords": ["capability", "registry", "crud", "sqlite"],
    },
]


def run_json(*args: str) -> Any:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def run_command(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    )


def compact_excerpt(text: str, *, limit: int = 420) -> str:
    excerpt = " ".join(text.split())
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 3].rstrip() + "..."


def load_done_tasks() -> dict[str, dict[str, Any]]:
    rows = run_json("python3", str(TASK_DB_SCRIPT), "task-list", "--planner-status", "done", "--json")
    done_tasks: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = str(row["task_id"])
        if task_id.endswith("-AUDIT"):
            continue
        done_tasks[task_id] = row
    return done_tasks


def load_existing_capabilities() -> dict[str, dict[str, Any]]:
    rows = run_json("python3", str(TASK_DB_SCRIPT), "capability-list", "--json")
    return {str(row["capability_id"]): row for row in rows}


def load_task_detail(task_id: str) -> dict[str, Any]:
    return run_json("python3", str(TASK_DB_SCRIPT), "task-show", "--task-id", task_id, "--json")


def build_payload(spec: dict[str, Any], task_detail: dict[str, Any]) -> dict[str, Any]:
    metadata = task_detail.get("metadata", {})
    closeout = metadata.get("closeout", {}) if isinstance(metadata, dict) else {}
    closeout_summary = str(closeout.get("summary") or "").strip()
    closeout_notes = str(closeout.get("notes") or "").strip()
    evidence_excerpt = compact_excerpt(closeout_notes or closeout_summary or str(task_detail.get("summary") or task_detail.get("title") or ""))
    closed_at = str(task_detail.get("closed_at") or task_detail.get("updated_at") or task_detail.get("created_at"))
    source_task_id = str(spec["source_task_id"])
    event_payload = {
        "bootstrap_mode": True,
        "override_kind": "bootstrap_bypass",
        "override_reason": BOOTSTRAP_REASON,
        "seeded_from_task_id": source_task_id,
        "seeded_from_task_title": task_detail.get("title"),
        "search_activation": "immediate",
        "closeout_excerpt": evidence_excerpt,
    }
    payload = {
        "capability_id": spec["capability_id"],
        "name": spec["name"],
        "summary": spec["summary"],
        "status": "active",
        "kind": spec["kind"],
        "scope_kind": spec["scope_kind"],
        "owning_repo_id": "CENTRAL",
        "when_to_use_md": spec["when_to_use_md"],
        "do_not_use_for_md": spec["do_not_use_for_md"],
        "entrypoints": spec["entrypoints"],
        "keywords": spec["keywords"],
        "affected_repo_ids": ["CENTRAL"],
        "evidence_summary_md": f"Seeded from {source_task_id} ({task_detail['title']}). {evidence_excerpt}",
        "verification_level": "planner_verified",
        "verified_by_task_id": source_task_id,
        "source_tasks": [{"task_id": source_task_id, "relationship_kind": "seeded_from"}],
        "metadata": {
            "bootstrap_mode": True,
            "override_kind": "bootstrap_bypass",
            "override_reason": BOOTSTRAP_REASON,
            "search_activation": "immediate",
            "seed_origin_task_id": source_task_id,
            "seed_origin_title": task_detail["title"],
            "seed_closeout_excerpt": evidence_excerpt,
        },
        "event_type": "capability.bootstrap_seeded",
        "event_payload": event_payload,
        "created_at": closed_at,
        "updated_at": closed_at,
    }
    return payload


def seed_capabilities(*, dry_run: bool) -> dict[str, Any]:
    done_tasks = load_done_tasks()
    existing = load_existing_capabilities()
    details_cache: dict[str, dict[str, Any]] = {}
    created: list[str] = []
    skipped_existing: list[str] = []
    source_task_ids: set[str] = set()

    for spec in CAPABILITY_SPECS:
        capability_id = str(spec["capability_id"])
        source_task_id = str(spec["source_task_id"])
        if source_task_id not in done_tasks:
            raise SystemExit(f"source task is not done or missing: {source_task_id}")
        source_task_ids.add(source_task_id)
        if capability_id in existing:
            skipped_existing.append(capability_id)
            continue
        task_detail = details_cache.get(source_task_id)
        if task_detail is None:
            task_detail = load_task_detail(source_task_id)
            details_cache[source_task_id] = task_detail
        payload = build_payload(spec, task_detail)
        if not dry_run:
            run_command(
                "python3",
                str(TASK_DB_SCRIPT),
                "capability-create",
                "--input",
                "-",
                "--json",
                input_text=json.dumps(payload),
            )
        created.append(capability_id)
        existing[capability_id] = {"capability_id": capability_id}

    final_rows = load_existing_capabilities() if not dry_run else existing
    return {
        "seed_spec_count": len(CAPABILITY_SPECS),
        "created_count": len(created),
        "skipped_existing_count": len(skipped_existing),
        "final_count": len(final_rows),
        "created_capability_ids": created,
        "skipped_existing_capability_ids": skipped_existing,
        "source_task_ids": sorted(source_task_ids),
        "platform_surfaces": [
            "dispatcher",
            "runtime_v2",
            "planner_ui",
            "cli_commands",
            "audit_lifecycle",
            "operator_tooling",
            "capability_registry",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview the seed plan without writing capability rows.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = seed_capabilities(dry_run=args.dry_run)
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(
            f"seed_spec_count={result['seed_spec_count']} created={result['created_count']} "
            f"skipped_existing={result['skipped_existing_count']} final_count={result['final_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
