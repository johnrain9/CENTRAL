#!/usr/bin/env python3
"""
task_quick.py — Streamlined task creation with templates and sensible defaults.

Usage:
    python3 scripts/task_quick.py --title "Fix login bug" --repo MOTO_HELPER
    python3 scripts/task_quick.py --title "Refactor auth module" --repo AIM_SOLO_ANALYSIS --template refactor
    python3 scripts/task_quick.py --title "Add dark mode" --repo PHOTO_AUTO_TAGGING --template feature --priority 60
    python3 scripts/task_quick.py --list-templates

Minimum required flags: --title and --repo (2 flags).
Templates: feature, bugfix, refactor, infrastructure (default: feature).
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
}

DEFAULT_TEMPLATE = "feature"
SERIES = "CENTRAL-OPS"
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
    task_id = get_next_task_id(SERIES)

    # Dispatch and closeout contracts use the resolved task_id
    dispatch = f"Dispatch from CENTRAL using repo={args.repo} do task {task_id}."
    closeout = f"Summarize results and closeout evidence for {task_id}."
    reconciliation = args.reconciliation or tpl["reconciliation"]

    cmd = [
        sys.executable, str(DB_SCRIPT), "planner-new",
        "--title", args.title,
        "--series", SERIES,
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
    print(f"  priority:  {args.priority if args.priority is not None else tpl['priority']}")
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
              python3 scripts/task_quick.py --list-templates
        """),
    )
    p.add_argument("--title", help="Task title (required unless --list-templates)")
    p.add_argument("--repo", help="Target repo ID or alias (required unless --list-templates)")
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
