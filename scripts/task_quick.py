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
import json
import subprocess
import sys
import textwrap
from pathlib import Path

TEMPLATES = {
    "feature": {
        "task_type": "feature",
        "priority": 50,
        "objective": "Implement the described feature with clean, tested code.",
        "context": "Feature requested via CENTRAL task system. See title and scope for specifics.",
        "scope": "Implement the feature as described. Include unit tests. Document any new public interfaces.",
        "deliverables": "Working implementation, passing tests, updated docs if applicable.",
        "acceptance": "Feature works as described. Tests pass. No regressions.",
        "testing": "Run the repo's standard test suite and verify the new feature is covered.",
        "reconciliation": "Summarize result and closeout evidence. Update CENTRAL canonical state.",
    },
    "bugfix": {
        "task_type": "bugfix",
        "priority": 70,
        "objective": "Diagnose and fix the described bug with a targeted, minimal change.",
        "context": "Bug reported via CENTRAL task system. See title for the symptom.",
        "scope": "Root-cause the bug, apply the minimal fix, and add a regression test.",
        "deliverables": "Bug fix commit, regression test, brief root-cause note in closeout.",
        "acceptance": "Bug no longer reproduces. Regression test passes. No new failures introduced.",
        "testing": "Reproduce the bug before the fix, confirm it is gone after. Run full suite.",
        "reconciliation": "Closeout with root-cause summary, test result, and fix ref.",
    },
    "refactor": {
        "task_type": "refactor",
        "priority": 40,
        "objective": "Improve the structure, readability, or performance of the targeted code without changing external behavior.",
        "context": "Refactor requested via CENTRAL task system. See title and scope for the target area.",
        "scope": "Refactor the described code. Preserve all existing behavior. Do not add features.",
        "deliverables": "Refactored code with all existing tests passing.",
        "acceptance": "External behavior unchanged. All tests pass. Code is demonstrably cleaner.",
        "testing": "Run the full test suite before and after. Both must pass with the same results.",
        "reconciliation": "Closeout with brief description of what changed and test results.",
    },
    "infrastructure": {
        "task_type": "infrastructure",
        "priority": 60,
        "objective": "Implement or improve the described infrastructure, tooling, or configuration.",
        "context": "Infrastructure task dispatched via CENTRAL. See title and scope for specifics.",
        "scope": "Implement the infrastructure change. Validate it works in the target environment.",
        "deliverables": "Working infrastructure change, validation evidence, updated docs if applicable.",
        "acceptance": "Infrastructure works as described and validated. No existing workflows broken.",
        "testing": "Validate the change end-to-end in the target environment. Document validation steps.",
        "reconciliation": "Closeout with validation evidence and any follow-on items.",
    },
    "design": {
        "task_type": "design",
        "priority": 30,
        "objective": "Produce a design brief or architecture decision that unblocks downstream implementation tasks.",
        "context": "Design task dispatched via CENTRAL. Output is a document or structured spec, not running code.",
        "scope": "Research options, evaluate trade-offs, and produce a written recommendation. Do not implement. Flag open questions explicitly.",
        "deliverables": "Design doc or decision record committed to the repo under docs/. Downstream task stubs if applicable.",
        "acceptance": "Design is complete, coherent, and actionable. Open questions are listed. Downstream tasks can be created from it.",
        "testing": "Peer-review checklist: objective is clear, scope is bounded, trade-offs are explicit, follow-on tasks are identified.",
        "reconciliation": "Closeout with doc path and a summary of key decisions. Propose follow-on implementation tasks.",
    },
    "docs": {
        "task_type": "docs",
        "priority": 35,
        "objective": "Create or update documentation so that the described system is clearly understood by AI and human readers.",
        "context": "Docs task dispatched via CENTRAL. See title and scope for the target artifact.",
        "scope": "Write or update the specified documentation. Do not change implementation code unless fixing a doc-code mismatch is explicitly in scope.",
        "deliverables": "Updated or new documentation file(s) committed to the repo.",
        "acceptance": "Documentation is accurate, complete for the described scope, and readable without additional context.",
        "testing": "Review against the implementation: confirm all described behaviors exist and no stale claims remain.",
        "reconciliation": "Closeout with doc path(s) and brief summary of what changed.",
    },
    "repo-health": {
        "task_type": "repo-health",
        "priority": 55,
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
        "objective": "Validate that the described system or feature meets its acceptance criteria in a real environment.",
        "context": "Validation task dispatched via CENTRAL. See title and scope for the target system.",
        "scope": "Run the specified validation steps end-to-end. Document results. Do not implement fixes — file follow-on tasks for any failures found.",
        "deliverables": "Validation report with pass/fail for each criterion, filed follow-on tasks for failures.",
        "acceptance": "All acceptance criteria are tested. Results are documented. Failures have corresponding follow-on tasks.",
        "testing": "The validation steps themselves are the test. Document the exact commands run and their outputs.",
        "reconciliation": "Closeout with validation report summary and list of any follow-on tasks created.",
    },
    "cleanup": {
        "task_type": "cleanup",
        "priority": 45,
        "objective": "Remove dead code, deprecated layers, or unused artifacts that add confusion without value.",
        "context": "Cleanup task dispatched via CENTRAL. See title and scope for what to remove.",
        "scope": "Remove only what is explicitly in scope. Verify nothing currently in use is deleted. Do not refactor adjacent code.",
        "deliverables": "Cleaned-up codebase with the described artifacts removed. All existing tests still pass.",
        "acceptance": "Targeted artifacts are removed. No live references remain. All tests pass.",
        "testing": "Run the full test suite before and after. Search for references to removed artifacts and confirm none remain.",
        "reconciliation": "Closeout with list of removed artifacts and test result confirmation.",
    },
    "planner-ops": {
        "task_type": "planner-ops",
        "priority": 50,
        "objective": "Implement or improve CENTRAL planner tooling, workflow scripts, or dispatch infrastructure.",
        "context": "Planner-ops task dispatched via CENTRAL. Target repo is CENTRAL unless otherwise specified.",
        "scope": "Implement the described planner tooling change. Preserve backward compatibility with existing planner workflows unless migration is explicitly in scope.",
        "deliverables": "Working tooling change committed to CENTRAL. Updated docs if the change affects planner workflow.",
        "acceptance": "Tooling works as described. Existing planner workflows are not broken. Docs reflect the change.",
        "testing": "Smoke-test the new or changed tooling end-to-end. Include the exact commands run and their outputs in closeout.",
        "reconciliation": "Closeout with command/result evidence and any follow-on items.",
    },
}

DEFAULT_TEMPLATE = "feature"
DEFAULT_SERIES = "CENTRAL-OPS"
DB_SCRIPT = Path(__file__).parent / "central_task_db.py"


def run(cmd: list[str], stdin: str | None = None) -> dict:
    result = subprocess.run(cmd, capture_output=True, text=True,
                            input=stdin)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    return json.loads(result.stdout)


def get_next_task_id(series: str) -> str:
    data = run([sys.executable, str(DB_SCRIPT), "task-id-next", "--series", series, "--json"])
    return data["next_task_id"]


def create_task(args: argparse.Namespace) -> None:
    template_name = args.template or DEFAULT_TEMPLATE
    if template_name not in TEMPLATES:
        print(f"Unknown template '{template_name}'. Available: {', '.join(TEMPLATES)}", file=sys.stderr)
        sys.exit(1)

    tpl = TEMPLATES[template_name]
    series = args.series or DEFAULT_SERIES
    task_id = get_next_task_id(series)

    # Dispatch and closeout contracts use the resolved task_id
    dispatch = f"Dispatch from CENTRAL using repo={args.repo} do task {task_id}."
    closeout = f"Summarize results and closeout evidence for {task_id}."
    reconciliation = args.reconciliation or tpl["reconciliation"]

    cmd = [
        sys.executable, str(DB_SCRIPT), "planner-new",
        "--title", args.title,
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

    if args.initiative:
        cmd += ["--initiative", args.initiative]

    if args.depends_on:
        for dep in args.depends_on:
            cmd += ["--depends-on", dep]

    # planner-new generates a scaffold; pipe it to task-create to persist
    scaffold = run(cmd)
    scaffold_json = json.dumps(scaffold)

    create_cmd = [
        sys.executable, str(DB_SCRIPT), "task-create",
        "--input", "-",
        "--json",
    ]
    created = run(create_cmd, stdin=scaffold_json)
    created_id = created.get("task_id", scaffold.get("task_id", task_id))
    print(f"Created {created_id}: {args.title}")
    print(f"  template:  {template_name}")
    print(f"  repo:      {args.repo}")
    print(f"  series:    {series}")
    print(f"  priority:  {args.priority if args.priority is not None else tpl['priority']}")
    if args.initiative:
        print(f"  initiative: {args.initiative}")
    print(f"  dispatch:  repo={args.repo} do task {created_id}")


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
