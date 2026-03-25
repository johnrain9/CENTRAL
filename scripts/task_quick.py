#!/usr/bin/env python3
"""
task_quick.py — Streamlined task creation with templates and sensible defaults.

Usage:
    python3 scripts/task_quick.py --title "Fix login bug" --repo MOTO_HELPER
    python3 scripts/task_quick.py --title "Refactor auth module" --repo AIM_SOLO_ANALYSIS --template refactor
    python3 scripts/task_quick.py --title "Add dark mode" --repo PHOTO_AUTO_TAGGING --template feature --priority 60
    python3 scripts/task_quick.py --list-templates

Minimum required flags: --title and --repo (2 flags).
Templates: feature, bugfix, refactor, infrastructure, design, docs, repo-health, validation, cleanup, planner-ops (default: feature).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Any

# Import canonicalize_task_intent directly so preflight intent exactly matches
# what task-create will verify against.
sys.path.insert(0, str(Path(__file__).parent))
from central_task_db import (  # noqa: E402
    canonicalize_task_intent,
    resolve_db_path,
    copy_sqlite_database,
)

TEMPLATES = {
    "feature": {
        "task_type": "feature",
        "priority": 50,
        "audit_required": True,
        "objective": "Implement the described feature with clean, tested code.",
        "context": "Feature requested via CENTRAL task system. See title and scope for specifics. An auditor will review your implementation for correctness and style.",
        "scope": "Implement the feature as described. Include unit tests. Document any new public interfaces. STOP BOUNDARY: if implementation requires product decisions not covered in the task scope, missing API contracts, or cross-subsystem redesign, stop and close out with blockers describing what decisions are needed — do not invent behavior.",
        "deliverables": "Working implementation, passing tests, updated docs if applicable.",
        "acceptance": "Feature works as described. Tests pass. No regressions.",
        "testing": "Run the repo's standard test suite and verify the new feature is covered.",
        "reconciliation": "Summarize result and closeout evidence. Update CENTRAL canonical state.",
    },
    "bugfix": {
        "task_type": "bugfix",
        "priority": 70,
        "audit_required": True,
        "objective": "Diagnose and fix the described bug with a targeted, minimal change.",
        "context": "Bug reported via CENTRAL task system. See title for the symptom. An auditor will review your fix for correctness and minimal blast radius.",
        "scope": "Root-cause the bug, apply the minimal fix, and add a regression test. STOP BOUNDARY: if the root cause is in a different subsystem than described, or the fix requires changing shared contracts or interfaces, stop and close out with blockers — do not expand scope beyond the described bug.",
        "deliverables": "Bug fix commit, regression test, brief root-cause note in closeout.",
        "acceptance": "Bug no longer reproduces. Regression test passes. No new failures introduced.",
        "testing": "Reproduce the bug before the fix, confirm it is gone after. Run full suite.",
        "reconciliation": "Closeout with root-cause summary, test result, and fix ref.",
    },
    "refactor": {
        "task_type": "refactor",
        "priority": 40,
        "audit_required": True,
        "objective": "Improve the structure, readability, or performance of the targeted code without changing external behavior.",
        "context": "Refactor requested via CENTRAL task system. See title and scope for the target area. An auditor will verify behavior is preserved.",
        "scope": "Refactor the described code. Preserve all existing behavior. Do not add features. STOP BOUNDARY: if the refactor reveals that the existing behavior is wrong, or requires changes to shared interfaces, stop and close out with findings — do not fix bugs or redesign interfaces as part of a refactor.",
        "deliverables": "Refactored code with all existing tests passing.",
        "acceptance": "External behavior unchanged. All tests pass. Code is demonstrably cleaner.",
        "testing": "Run the full test suite before and after. Both must pass with the same results.",
        "reconciliation": "Closeout with brief description of what changed and test results.",
    },
    "infrastructure": {
        "task_type": "infrastructure",
        "priority": 60,
        "audit_required": True,
        "objective": "Implement or improve the described infrastructure, tooling, or configuration.",
        "context": "Infrastructure task dispatched via CENTRAL. See title and scope for specifics. An auditor will verify the change works and doesn't break existing workflows.",
        "scope": "Implement the infrastructure change. Validate it works in the target environment. STOP BOUNDARY: if the change requires modifying production systems, changing CI/CD pipelines that affect other repos, or altering shared configuration, stop and close out with a plan for review — do not make changes with broad blast radius without explicit approval in the task scope.",
        "deliverables": "Working infrastructure change, validation evidence, updated docs if applicable.",
        "acceptance": "Infrastructure works as described and validated. No existing workflows broken.",
        "testing": "Validate the change end-to-end in the target environment. Document validation steps.",
        "reconciliation": "Closeout with validation evidence and any follow-on items.",
    },
    "design": {
        "task_type": "design",
        "priority": 30,
        "audit_required": False,
        "objective": "Produce a design brief or architecture decision that unblocks downstream implementation tasks.",
        "context": "Design task dispatched via CENTRAL. Output is a document or structured spec, not running code. For LLDs: follow docs/lld_worker_guidelines.md — especially §1.3 (forced decisions), §3 (trace before fixing), §5 (follow-on task table and task creation). For HLDs: follow docs/hld_worker_guidelines.md — especially §1.3 (forced decisions), §5 (required LLD table and LLD task creation).",
        "scope": "Research options, evaluate trade-offs, and produce a written recommendation. Do not implement. Flag open questions explicitly. If scope exceeds 5 major subsystems, propose a split. AUTHORITY BOUNDARY: you may create downstream implementation/LLD tasks as specified in the guidelines, but sequencing, priority assignment, initiative fit, and whether this design should pause or redirect other active work are operator/L3 decisions — note recommendations but do not act on them.",
        "deliverables": "Design doc committed to the repo under docs/. For LLDs: structured follow-on task table as the last section with all implementation tasks created in CENTRAL. For HLDs: required LLD table as the last section with all LLD design tasks created in CENTRAL. See guidelines §5.",
        "acceptance": "Design is complete, coherent, and actionable. Open questions are listed with owners. Cross-references are internally consistent. Review tool has been run (see guidelines §4). Task table covers all downstream work implied by the design body. All downstream tasks exist in CENTRAL.",
        "testing": "Peer-review checklist: objective is clear, scope is bounded, trade-offs are explicit, forced design decisions are answered, task table is complete, all downstream tasks created in CENTRAL.",
        "reconciliation": "Closeout with doc path, summary of key decisions, and list of created downstream task IDs.",
    },
    "docs": {
        "task_type": "docs",
        "priority": 35,
        "audit_required": False,
        "objective": "Create or update documentation so that the described system is clearly understood by AI and human readers.",
        "context": "Docs task dispatched via CENTRAL. See title and scope for the target artifact. Documentation is consumed by both AI workers (who read AI_GUIDE.md, AI_UI_GUIDE.md) and human operators — ensure it is accurate and actionable for both audiences.",
        "scope": "Write or update the specified documentation. Do not change implementation code unless fixing a doc-code mismatch is explicitly in scope.",
        "deliverables": "Updated or new documentation file(s) committed to the repo.",
        "acceptance": "Documentation is accurate, complete for the described scope, and readable without additional context.",
        "testing": "Review against the implementation: confirm all described behaviors exist and no stale claims remain.",
        "reconciliation": "Closeout with doc path(s) and brief summary of what changed.",
    },
    "repo-health": {
        "task_type": "repo-health",
        "priority": 55,
        "audit_required": True,
        "objective": "Implement or update a repo health adapter so the repo reports status correctly to the CENTRAL health system.",
        "context": "Repo health task dispatched via CENTRAL. See docs/repo_health_adapter_contract.md for the contract.",
        "scope": "Implement the health adapter per the CENTRAL contract. Register the repo if not already registered. Validate the adapter returns correct status.",
        "deliverables": "Working health adapter, repo registered in CENTRAL registry, validation evidence.",
        "acceptance": "Adapter returns valid health status. CENTRAL can query the repo health without error. No existing health checks broken.",
        "testing": "Run python3 scripts/repo_health.py for the target repo and confirm a valid JSON response. Run CENTRAL health check end-to-end.",
        "reconciliation": "Closeout with adapter path, registry entry, and health check output.",
    },
    "validation": {
        "task_type": "validation",
        "priority": 65,
        "audit_required": False,
        "objective": "Validate that the described system or feature meets its acceptance criteria in a real environment. Do NOT make code changes or create follow-on tasks.",
        "context": "Validation task dispatched via CENTRAL. See title and scope for the target system. An L2 lieutenant or operator will review your results and decide on next actions.",
        "scope": "Run the specified validation steps end-to-end. Document results with pass/fail for each criterion. Do not implement fixes. Do not create follow-on tasks — the reviewer will handle that based on your report.",
        "deliverables": "A structured validation report in the task closeout summary with pass/fail for each acceptance criterion, exact commands run, and output evidence.",
        "acceptance": "All acceptance criteria are tested. Results are documented with enough detail for someone else to act on failures.",
        "testing": "The validation steps themselves are the test. Document the exact commands run and their outputs.",
        "reconciliation": "Closeout with the full validation report. Do NOT create follow-on tasks — the reviewer will handle that.",
    },
    "cleanup": {
        "task_type": "cleanup",
        "priority": 45,
        "audit_required": False,
        "objective": "Remove dead code, deprecated layers, or unused artifacts that add confusion without value.",
        "context": "Cleanup task dispatched via CENTRAL. See title and scope for what to remove. L2 gates will verify the build still passes after cleanup.",
        "scope": "Remove only what is explicitly in scope. Verify nothing currently in use is deleted. Do not refactor adjacent code.",
        "deliverables": "Cleaned-up codebase with the described artifacts removed. All existing tests still pass.",
        "acceptance": "Targeted artifacts are removed. No live references remain. All tests pass.",
        "testing": "Run the full test suite before and after. Search for references to removed artifacts and confirm none remain.",
        "reconciliation": "Closeout with list of removed artifacts and test result confirmation.",
    },
    "planner-ops": {
        "task_type": "planner-ops",
        "priority": 50,
        "audit_required": True,
        "objective": "Implement or improve CENTRAL planner tooling, workflow scripts, or dispatch infrastructure.",
        "context": "Planner-ops task dispatched via CENTRAL. Target repo is CENTRAL unless otherwise specified. An auditor will verify the change works and existing workflows are intact. The operator and L3 chief of staff consume planner tooling — changes affect how work flows through the system.",
        "scope": "Implement the described planner tooling change. Preserve backward compatibility with existing planner workflows unless migration is explicitly in scope.",
        "deliverables": "Working tooling change committed to CENTRAL. Updated docs if the change affects planner workflow.",
        "acceptance": "Tooling works as described. Existing planner workflows are not broken. Docs reflect the change.",
        "testing": "Smoke-test the new or changed tooling end-to-end. Include the exact commands run and their outputs in closeout.",
        "reconciliation": "Closeout with command/result evidence and any follow-on items.",
    },
    "investigation": {
        "task_type": "investigation",
        "priority": 55,
        "audit_required": False,
        "objective": "Investigate the described problem, diagnose root causes, and produce a structured findings report. Do NOT make code changes or create follow-on tasks.",
        "context": "Investigation task dispatched via CENTRAL. An L2 lieutenant or operator will review your findings and decide on next actions. Your job is accurate diagnosis, not fixing. Before investigating, load project context: read AI_GUIDE.md and AI_UI_GUIDE.md in the repo root for architecture and conventions. For frontend investigations, also read the relevant LLD docs under docs/design/lld/. Use git log and git blame to understand recent changes to the area under investigation. Check for worker summaries in .task-context/ if present.",
        "scope": "Investigate the described area. Identify root causes, classify failures, and document findings. Explicitly out of scope: code fixes, follow-on task creation, and architectural recommendations beyond what the evidence supports.",
        "deliverables": "A structured findings report written to the task closeout summary. Report must include: (1) what was investigated, (2) root causes found, (3) failure classification (e.g., missing implementation, stale test, wrong mock, real bug), (4) severity assessment, (5) suggested fix approach for each finding (one line each, enough for a task creator to act on).",
        "acceptance": "Every item in scope is investigated. Root causes are identified, not just symptoms. Findings are specific enough for someone else to create actionable fix tasks from them.",
        "testing": "Reproduce each failure. Confirm root cause by tracing from symptom to source. Note which failures share a common root cause.",
        "reconciliation": "Closeout with the full structured findings report. Do NOT create follow-on tasks — the reviewer will handle that.",
    },
}

DEFAULT_TEMPLATE = "feature"
DEFAULT_SERIES = "CENTRAL-OPS"
DEFAULT_INITIATIVE = "one-off"
DB_SCRIPT = Path(__file__).parent / "central_task_db.py"
DEFAULT_DB_PATH = resolve_db_path(None)
SMOKE_TITLE_MARKER = "[planner-ops-smoke:"


def run(
    cmd: list[str],
    stdin: str | None = None,
    db_path: str | Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict:
    run_env = os.environ.copy()
    if db_path is not None:
        run_env["CENTRAL_TASK_DB_PATH"] = str(db_path)
    if env_overrides:
        run_env.update({k: str(v) for k, v in env_overrides.items()})
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin, env=run_env)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"Command did not return JSON: {exc}", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        sys.exit(1)


def get_next_task_id(series: str, db_path: str | Path | None = None) -> str:
    data = run(
        [sys.executable, str(DB_SCRIPT), "task-id-next", "--series", series, "--json"],
        db_path=db_path,
    )
    return data["next_task_id"]


# Repos where capability preflight is meaningful — ops/platform only.
# Product repos skip capability search (empty repo_ids) so preflight is a
# fast passthrough that just issues a valid token without redundancy checks.
PLATFORM_REPOS = {"CENTRAL", "Dispatcher"}


def run_preflight(scaffold: dict, db_path: str | Path | None = None) -> dict:
    """Run task-preflight and return the full response.

    Builds the intent from the scaffold using the same canonicalize function
    that task-create uses internally, so the token is always valid.

    Only called for platform repos (CENTRAL, Dispatcher). Product repos skip
    preflight entirely via --skip-preflight in task-create.
    """
    intent = canonicalize_task_intent(scaffold)
    repo_id = str(scaffold.get("target_repo_id") or "CENTRAL").strip() or "CENTRAL"
    preflight_request = {
        "normalized_task_intent": intent,
        "request_context": {
            "existing_task_id": None,
            "existing_task_version": None,
            "is_material_update": False,
            "request_channel": "task-create",
            "requested_by": "planner/coordinator",
        },
        "search_scope": {
            "include_active_tasks": True,
            "include_capabilities": True,
            "include_deprecated_capabilities": False,
            "include_recent_done_days": 90,
            "max_candidates_per_kind": 50,
            "repo_ids": sorted({repo_id, "CENTRAL"}),
        },
    }
    pf = run(
        [sys.executable, str(DB_SCRIPT), "task-preflight", "--input", "-", "--json"],
        stdin=json.dumps(preflight_request),
        db_path=db_path,
    )
    pf["_request"] = preflight_request  # stash for use in create payload
    return pf


def ensure_unique_smoke_title(title: str, task_id: str) -> str:
    cleaned = title.strip()
    if SMOKE_TITLE_MARKER in cleaned:
        return cleaned
    suffix = uuid.uuid4().hex[:8]
    return f"{cleaned} {SMOKE_TITLE_MARKER}{task_id}-{suffix}]"


def attach_preflight(scaffold: dict, pf: dict, novelty_rationale: str) -> dict:
    """Inject the preflight block (and override if needed) into the scaffold."""
    blocking_bucket = pf.get("blocking_bucket", "none")
    classification_options = pf.get("classification_options", ["new"])
    override_kind = pf.get("override_kind", "none")

    # Pick the least-surprising classification automatically:
    # prefer "new" when unblocked, otherwise "follow_on".
    if "new" in classification_options:
        classification = "new"
    elif "follow_on" in classification_options:
        classification = "follow_on"
    else:
        classification = classification_options[0]

    scaffold["preflight"] = {
        "request": pf["_request"],
        "response": {k: v for k, v in pf.items() if k != "_request"},
        "preflight_token": pf["preflight_token"],
        "classification": classification,
        "novelty_rationale": novelty_rationale,
    }

    if blocking_bucket in ("strong_overlap", "weak_overlap") and override_kind != "none":
        # Acknowledge ALL candidates (strong and weak) — task-create validates completeness.
        all_ids = [
            c["candidate_id"] for c in pf.get("candidates", [])
        ]
        scaffold["override"] = {
            "override_kind": override_kind,
            "override_reason": (
                "Planner-reviewed: overlap candidates are false positives — "
                "keyword matches on unrelated capabilities. Task addresses a distinct "
                "problem not covered by existing work."
            ),
            "override_actor_id": "planner/coordinator",
            "override_authority": "planner",
            "acknowledged_candidate_ids": all_ids,
        }

    return scaffold


def print_planner_ops_smoke(
    task_id: str,
    template: str,
    repo: str,
    series: str,
    tpl: dict[str, Any],
    args: argparse.Namespace,
    pf: dict[str, Any],
    smoke_db: str | Path | None = None,
    created: dict[str, Any] | None = None,
) -> None:
    priority = args.priority if args.priority is not None else tpl["priority"]
    candidates = pf.get("candidates", [])
    strong_overlap = sum(1 for c in candidates if c.get("band") == "strong_overlap")
    weak_overlap = sum(1 for c in candidates if c.get("band") == "weak_overlap")
    alpha = build_alpha(task_id, pf["preflight_token"])

    print("Planner-ops preflight smoke: pass")
    print(f"  task_id:      {task_id}")
    print(f"  template:     {template}")
    print(f"  repo:         {repo}")
    print(f"  series:       {series}")
    print(f"  priority:     {priority}")
    print(f"  preflight:    {pf['blocking_bucket']} (token: {pf['preflight_token'][:24]}...)")
    print(f"  alpha:        {alpha}")
    print(f"  candidates:   {len(candidates)} (strong={strong_overlap}, weak={weak_overlap})")
    if created is not None:
        print(f"  created_id:   {created.get('task_id', task_id)}")
        print(f"  created_state:{created.get('planner_status', '?')}")
        print(f"  created_ver:  v{created.get('version', '?')}")
    if smoke_db:
        print(f"  smoke_db:     {smoke_db}")
    if args.initiative:
        print(f"  initiative:   {args.initiative}")
    if created is not None:
        print("  state:        preflight + task-create validated in smoke DB (no canonical DB write)")
    else:
        print("  state:        preflight validated, no canonical DB write")
    if smoke_db:
        print("  cleanup:      temp smoke DB removed after validation")


def build_alpha(task_id: str, preflight_token: str) -> str:
    alpha = hashlib.sha256(f"{task_id}:{preflight_token}".encode("utf-8")).hexdigest()[:10]
    return f"alpha-{alpha}"


def create_task(args: argparse.Namespace) -> None:
    template_name = args.template or DEFAULT_TEMPLATE
    if template_name not in TEMPLATES:
        print(f"Unknown template '{template_name}'. Available: {', '.join(TEMPLATES)}", file=sys.stderr)
        sys.exit(1)
    if args.planner_ops_smoke and template_name != "planner-ops":
        print("--planner-ops-smoke can only be used with --template planner-ops", file=sys.stderr)
        sys.exit(1)

    tpl = TEMPLATES[template_name]
    series = args.series or DEFAULT_SERIES
    is_smoke = args.planner_ops_smoke or args.dry_run

    initiative = args.initiative or DEFAULT_INITIATIVE

    source_db_path = resolve_db_path(args.db_path)
    smoke_dir = None
    db_path: str | Path | None = args.db_path
    if args.planner_ops_smoke:
        if not source_db_path.exists():
            print(
                "planner-ops-smoke requires a readable source CENTRAL DB for copy-based testing.",
                file=sys.stderr,
            )
            sys.exit(1)
        smoke_dir = tempfile.mkdtemp(prefix="central-task-smoke-")
        db_path = Path(smoke_dir) / "central_tasks_smoke.db"
        copy_sqlite_database(source_db_path, db_path)
        task_id = get_next_task_id(series, db_path=db_path)
    else:
        task_id = get_next_task_id(series, db_path=db_path)

    smoke_title = args.title
    if args.planner_ops_smoke:
        smoke_title = ensure_unique_smoke_title(args.title, task_id)

    dispatch = f"Dispatch from CENTRAL using repo={args.repo} do task {task_id}."
    closeout = f"Summarize results and closeout evidence for {task_id}."
    reconciliation = args.reconciliation or tpl["reconciliation"]

    planner_new_cmd = [
        sys.executable, str(DB_SCRIPT), "planner-new",
        "--title", smoke_title,
        "--series", series,
        "--repo", args.repo,
        "--task-type", args.task_type or tpl["task_type"],
        "--priority", str(args.priority if args.priority is not None else tpl["priority"]),
        "--objective", args.objective or tpl["objective"],
        "--context", args.context or tpl["context"],
        "--scope", args.scope or tpl["scope"],
        "--deliverables", args.deliverables or tpl["deliverables"],
        "--acceptance", args.acceptance or tpl["acceptance"],
        "--testing", args.testing or tpl["testing"],
        "--dispatch", dispatch,
        "--closeout", closeout,
        "--reconciliation", reconciliation,
        "--json",
    ]

    if initiative:
        planner_new_cmd += ["--initiative", initiative]

    if args.depends_on:
        for dep in args.depends_on:
            planner_new_cmd += ["--depends-on", dep]

    try:
        scaffold = run(planner_new_cmd, db_path=db_path)
        task_id = scaffold.get("task_id", task_id)

        # Wire worker backend/model/effort overrides into execution metadata if provided.
        if args.worker_backend or args.worker_model or args.effort:
            execution = scaffold.get("execution") or {}
            exec_meta = execution.get("metadata") or {}
            if args.worker_backend:
                exec_meta["worker_backend"] = args.worker_backend
            if args.worker_model:
                exec_meta["worker_model"] = args.worker_model
            if args.effort:
                exec_meta["worker_effort"] = args.effort
            execution["metadata"] = exec_meta
            scaffold["execution"] = execution

        # Wire audit_required into scaffold metadata — required, no defaults.
        if "audit_required" not in tpl:
            print(f"FATAL: template '{template_name}' is missing required 'audit_required' field", file=sys.stderr)
            sys.exit(1)
        scaffold_metadata = scaffold.get("metadata") or {}
        if args.remote:
            scaffold_metadata["remote"] = True
            scaffold_metadata["remote_only"] = True
        scaffold_metadata["audit_required"] = tpl["audit_required"]
        scaffold["metadata"] = scaffold_metadata
        is_platform_repo = (args.repo in PLATFORM_REPOS)
        if is_platform_repo:
            pf = run_preflight(scaffold, db_path=db_path)
            alpha = build_alpha(task_id, pf["preflight_token"])
            novelty_rationale = (
                args.novelty_rationale
                or f"New {args.task_type or tpl['task_type']} task: {args.title}"
            )
            scaffold = attach_preflight(scaffold, pf, novelty_rationale)
        else:
            pf = {"blocking_bucket": "skipped", "preflight_token": "skipped"}
            alpha = "n/a"

        if is_smoke:
            if args.planner_ops_smoke:
                created = run(
                    [sys.executable, str(DB_SCRIPT), "task-create", "--input", "-", "--json"],
                    stdin=json.dumps(scaffold),
                    db_path=db_path,
                )
                print_planner_ops_smoke(
                    task_id,
                    template_name,
                    args.repo,
                    series,
                    tpl,
                    args,
                    pf,
                    smoke_db=db_path,
                    created=created,
                )
                return

            print(f"Dry-run: {args.title}")
            print(f"  task_id:   {task_id}")
            print(f"  template:  {template_name}")
            print(f"  repo:      {args.repo}")
            print(f"  series:    {series}")
            print(f"  priority:  {args.priority if args.priority is not None else tpl['priority']}")
            preflight_display = "skipped (product repo)" if not is_platform_repo else f"{pf['blocking_bucket']} (token: {pf['preflight_token'][:24]}...)"
            print(f"  preflight: {preflight_display}")
            print(f"  alpha:     {alpha}")
            if args.initiative:
                print(f"  initiative: {args.initiative}")
            print("  state:     preflight validated, no write performed")
            return

        task_create_cmd = [sys.executable, str(DB_SCRIPT), "task-create", "--input", "-", "--json"]
        if not is_platform_repo:
            task_create_cmd.append("--skip-preflight")
        created = run(
            task_create_cmd,
            stdin=json.dumps(scaffold),
            db_path=db_path,
        )
        created_id = created.get("task_id", scaffold.get("task_id", task_id))
        print(f"Created {created_id}: {args.title}")
        print(f"  template:  {template_name}")
        print(f"  repo:      {args.repo}")
        print(f"  series:    {series}")
        print(f"  priority:  {args.priority if args.priority is not None else tpl['priority']}")
        if args.initiative:
            print(f"  initiative: {args.initiative}")
        print(f"  dispatch:  repo={args.repo} do task {created_id}")
    finally:
        if smoke_dir is not None and not args.planner_ops_smoke:
            shutil.rmtree(smoke_dir, ignore_errors=True)


def list_templates() -> None:
    print("Available templates:\n")
    for name, tpl in TEMPLATES.items():
        marker = " (default)" if name == DEFAULT_TEMPLATE else ""
        print(f"  {name}{marker}")
        print(f"    task_type: {tpl['task_type']}")
        print(f"    priority:  {tpl['priority']}")
        print(f"    objective: {tpl['objective'][:80]}...")
        print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="task_quick.py",
        description="Streamlined CENTRAL task creation. Minimum: --title and --repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 scripts/task_quick.py --title "Fix login bug" --repo MOTO_HELPER
              python3 scripts/task_quick.py --title "Add export API" --repo AIM_SOLO_ANALYSIS --template feature
              python3 scripts/task_quick.py --title "Refactor DB layer" --repo CENTRAL --template refactor --priority 55
              python3 scripts/task_quick.py --title "CI pipeline" --repo CENTRAL --template infrastructure
              python3 scripts/task_quick.py --title "Design auth overhaul" --repo CENTRAL --template design
              python3 scripts/task_quick.py --title "Write README" --repo MOTO_HELPER --template docs
              python3 scripts/task_quick.py --title "Health adapter" --repo PHOTO_AUTO_TAGGING --template repo-health
              python3 scripts/task_quick.py --title "Validate voice PTT" --repo CENTRAL --template validation
              python3 scripts/task_quick.py --title "Remove deprecated layer" --repo CENTRAL --template cleanup
              python3 scripts/task_quick.py --title "Add planner macro tool" --repo CENTRAL --template planner-ops
              python3 scripts/task_quick.py --list-templates
        """),
    )
    p.add_argument("--title", help="Task title (required unless --list-templates)")
    p.add_argument("--repo", help="Target repo ID or alias (required unless --list-templates)")
    p.add_argument("--db-path", default=None, help="Optional CENTRAL DB path override")
    p.add_argument("--series", default=None,
                   help=f"Task ID series for allocation. Default: {DEFAULT_SERIES}")
    p.add_argument("--template", choices=list(TEMPLATES), default=None,
                   help=f"Task template. Default: {DEFAULT_TEMPLATE}")
    p.add_argument("--priority", type=int, default=None, help="Override priority (0-100)")
    p.add_argument("--task-type", default=None, help="Override task_type")
    p.add_argument("--objective", default=None, help="Override objective")
    p.add_argument("--context", default=None, help="Override context")
    p.add_argument("--scope", default=None, help="Override scope")
    p.add_argument("--deliverables", default=None, help="Override deliverables")
    p.add_argument("--acceptance", default=None, help="Override acceptance criteria")
    p.add_argument("--testing", default=None, help="Override testing section")
    p.add_argument("--reconciliation", default=None, help="Override reconciliation section")
    p.add_argument("--depends-on", action="append", metavar="TASK_ID",
                   help="Dependency task ID (repeatable)")
    p.add_argument("--initiative", default=None,
                   help="Optional initiative/epic tag for grouping (e.g. 'dispatcher-infrastructure')")
    p.add_argument("--worker-backend", default=None, choices=["codex", "claude", "gemini", "grok", "stub"],
                   help="Route task to a specific worker backend (default: dispatcher default)")
    p.add_argument("--worker-model", default=None,
                   help="Override the worker model for this task (e.g. 'gemini-3-pro-preview')")
    p.add_argument("--remote", action="store_true", help="Route task to remote workers only")
    p.add_argument("--effort", default=None, choices=["low", "medium", "high", "max"],
                   help="Reasoning effort level for this task (applies to codex and claude backends)")
    p.add_argument("--dry-run", action="store_true", help="Run scaffold + preflight and validate, then exit without writing task.")
    p.add_argument("--planner-ops-smoke", action="store_true",
                   help="Run planner-ops preflight smoke validation and exit without writing task.")
    p.add_argument("--novelty-rationale", default=None,
                   help="Why this task is new/distinct (used in preflight). Auto-generated if omitted.")
    p.add_argument("--list-templates", action="store_true", help="List available templates and exit")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_templates:
        list_templates()
        return

    if not args.title:
        parser.error("--title is required")
    if not args.repo:
        parser.error("--repo is required")

    create_task(args)


if __name__ == "__main__":
    main()
