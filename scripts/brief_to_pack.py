#!/usr/bin/env python3
"""
brief_to_pack.py — Design-brief to draft task-pack generator.

Reads a structured design brief (YAML file or stdin) and expands each task
stub into a fully-populated task payload using the same templates as
task_quick.py.  Outputs a draft pack YAML for planner review before any
tasks are committed to the DB.

Usage:
    # Generate draft from a brief file (review only):
    python3 scripts/brief_to_pack.py --brief docs/examples/briefs/auth_overhaul.yaml

    # Generate and write draft to a file:
    python3 scripts/brief_to_pack.py --brief my_brief.yaml --output /tmp/draft_pack.yaml

    # Generate, review, and commit in one shot:
    python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit

    # Dry-run (shows IDs that would be allocated, no DB writes):
    python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit --dry-run

    # Skip interactive confirmation and commit immediately:
    python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit --yes

Brief format (YAML):
    title: "Feature X"           # workstream title (used for context prefix)
    repo: SOME_REPO              # default repo for all tasks
    series: CENTRAL-OPS          # default task ID series
    context: "..."               # shared context prepended to each task
    priority: 50                 # default priority override
    tasks:
      - title: "Design the module"
        template: design         # any task_quick template name
        priority: 70             # optional per-task override
        context: "..."           # optional per-task context suffix
        objective: "..."         # optional full override
        scope: "..."
        deliverables: "..."
        acceptance: "..."
        testing: "..."
        depends_on: [previous]   # list of task IDs or "previous"
      - title: "Implement the module"
        template: feature
        depends_on: [previous]   # "previous" = the task immediately above
"""

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Re-use the same template library as task_quick.py so they stay in sync.
# Import from task_quick if available; otherwise inline a minimal copy.
_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from task_quick import TEMPLATES  # type: ignore[import]
except ImportError:
    TEMPLATES = {}  # type: ignore[assignment]

DB_SCRIPT = _SCRIPTS_DIR / "central_task_db.py"

# ---------------------------------------------------------------------------
# Brief loading
# ---------------------------------------------------------------------------

def _load_brief(path: str) -> dict:
    if yaml is None:
        _die("PyYAML is required: pip install pyyaml")
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        _die("Brief must be a YAML mapping at the top level.")
    return data


# ---------------------------------------------------------------------------
# Task expansion
# ---------------------------------------------------------------------------

def _expand_tasks(brief: dict) -> list[dict]:
    """Return a list of fully-expanded task dicts ready for batch-create."""
    brief_title = brief.get("title", "Unnamed workstream")
    brief_context = brief.get("context", "")
    default_repo = brief.get("repo", "CENTRAL")
    default_series = brief.get("series", "CENTRAL-OPS")
    default_priority = brief.get("priority")

    task_stubs = brief.get("tasks", [])
    if not task_stubs:
        _die("Brief contains no tasks.")

    expanded: list[dict] = []
    previous_id: str | None = None  # for depends_on: [previous]

    for i, stub in enumerate(task_stubs):
        title = stub.get("title") or _die(f"Task {i+1} is missing a title.")
        template_name = stub.get("template", "feature")
        if template_name not in TEMPLATES:
            _die(
                f"Task '{title}': unknown template '{template_name}'. "
                f"Available: {', '.join(TEMPLATES)}"
            )
        tpl = TEMPLATES[template_name]

        # Build context: shared brief context + optional per-task suffix
        ctx_parts = []
        if brief_context:
            ctx_parts.append(brief_context.strip())
        if stub.get("context"):
            ctx_parts.append(stub["context"].strip())
        if not ctx_parts:
            ctx_parts.append(tpl["context"])
        task_context = "\n\n".join(ctx_parts)

        # Priority: per-task > brief-level > template default
        priority = stub.get("priority") or default_priority or tpl["priority"]

        # Resolve depends_on: "previous" keyword → previous task's placeholder
        raw_deps = stub.get("depends_on", [])
        if isinstance(raw_deps, str):
            raw_deps = [raw_deps]
        deps: list[str] = []
        for dep in raw_deps:
            if dep == "previous":
                if previous_id is not None:
                    deps.append(previous_id)
                # else: first task, skip silently
            else:
                deps.append(dep)

        # Build the expanded task record (batch-create format)
        task: dict = {
            "title": title,
            "repo": stub.get("repo", default_repo),
            "series": stub.get("series", default_series),
            "task_type": stub.get("task_type", tpl["task_type"]),
            "priority": priority,
            "objective": stub.get("objective") or tpl["objective"],
            "context": task_context,
            "scope": stub.get("scope") or tpl["scope"],
            "deliverables": stub.get("deliverables") or tpl["deliverables"],
            "acceptance": stub.get("acceptance") or tpl["acceptance"],
            "testing": stub.get("testing") or tpl["testing"],
            "reconciliation": stub.get("reconciliation") or tpl.get("reconciliation", ""),
        }
        if deps:
            task["depends_on"] = deps

        # Placeholder so "previous" can reference this task before IDs are
        # assigned.  The placeholder is a positional label; it will be replaced
        # with the real task ID after a dry-run allocation.
        placeholder = f"__task_{i}__"
        previous_id = placeholder
        task["_placeholder"] = placeholder

        expanded.append(task)

    return expanded


# ---------------------------------------------------------------------------
# ID allocation via dry-run
# ---------------------------------------------------------------------------

def _allocate_ids(tasks: list[dict], dry_run: bool = True) -> list[dict]:
    """
    Run task-batch-create --dry-run to preview the IDs that would be
    allocated, then substitute placeholders in depends_on fields.
    """
    pack = _build_pack(tasks)
    pack_yaml = _dump_yaml(pack)

    cmd = [
        sys.executable, str(DB_SCRIPT), "task-batch-create",
        "--input", "-",
        "--dry-run",
    ]
    result = subprocess.run(cmd, input=pack_yaml, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        _die("ID pre-allocation dry-run failed.")

    # Parse the dry-run output to extract task_id → placeholder mapping
    try:
        dry_out = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Non-JSON output — IDs not available, skip substitution
        return tasks

    allocated: dict[str, str] = {}  # placeholder → real_id
    # task-batch-create --dry-run returns {"results": [{task_id, index, ...}]}
    rows = dry_out if isinstance(dry_out, list) else dry_out.get("results", [])
    for item in rows:
        idx = item.get("index")
        real_id = item.get("task_id", "")
        if idx is not None and idx < len(tasks) and real_id:
            ph = tasks[idx].get("_placeholder", "")
            if ph:
                allocated[ph] = real_id

    # Substitute placeholders in depends_on
    result_tasks = []
    for task in tasks:
        t = dict(task)
        if "depends_on" in t:
            t["depends_on"] = [
                allocated.get(dep, dep) for dep in t["depends_on"]
            ]
        result_tasks.append(t)
    return result_tasks


def _build_pack(tasks: list[dict]) -> dict:
    """Build the batch-create YAML structure, stripping internal keys."""
    clean_tasks = []
    for t in tasks:
        ct = {k: v for k, v in t.items() if not k.startswith("_")}
        clean_tasks.append(ct)
    return {"tasks": clean_tasks}


# ---------------------------------------------------------------------------
# YAML output
# ---------------------------------------------------------------------------

def _dump_yaml(data: dict) -> str:
    if yaml is None:
        _die("PyYAML is required: pip install pyyaml")
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _print_draft(tasks: list[dict], brief: dict) -> None:
    """Pretty-print the draft pack for human review."""
    print("=" * 70)
    print(f"DRAFT TASK PACK — {brief.get('title', 'Unnamed workstream')}")
    print(f"repo: {brief.get('repo', 'CENTRAL')}  series: {brief.get('series', 'CENTRAL-OPS')}")
    print("=" * 70)
    for i, t in enumerate(tasks, 1):
        deps = t.get("depends_on", [])
        dep_str = f"  depends_on: {deps}" if deps else ""
        print(f"\n  [{i}] {t['title']}")
        print(f"      template/type: {t['task_type']}  priority: {t['priority']}{dep_str}")
        # Show objective truncated
        obj = t.get("objective", "")
        if len(obj) > 120:
            obj = obj[:117] + "..."
        print(f"      objective: {obj}")
    print()


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def _commit_pack(tasks: list[dict], dry_run: bool = False) -> None:
    pack = _build_pack(tasks)
    pack_yaml = _dump_yaml(pack)

    cmd = [sys.executable, str(DB_SCRIPT), "task-batch-create", "--input", "-"]
    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, input=pack_yaml, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        _die("task-batch-create failed.")
    print(result.stdout)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="brief_to_pack.py",
        description="Convert a design brief into a draft task pack for planner review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Review only (default):
              python3 scripts/brief_to_pack.py --brief docs/examples/briefs/auth_overhaul.yaml

              # Save draft to file:
              python3 scripts/brief_to_pack.py --brief my_brief.yaml --output /tmp/draft.yaml

              # Review and commit interactively:
              python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit

              # Commit without confirmation:
              python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit --yes

              # Preview IDs, no DB writes:
              python3 scripts/brief_to_pack.py --brief my_brief.yaml --commit --dry-run

            Brief format (YAML):
              title: "Feature X"
              repo: SOME_REPO
              series: CENTRAL-OPS
              context: "Shared context for all tasks."
              tasks:
                - title: "Design the module"
                  template: design
                  priority: 70
                - title: "Implement the module"
                  template: feature
                  depends_on: [previous]
        """),
    )
    p.add_argument("--brief", default="-",
                   help="Path to a YAML design brief, or - for stdin. Default: -")
    p.add_argument("--output", default=None,
                   help="Write expanded draft YAML to this file. Default: print to stdout.")
    p.add_argument("--commit", action="store_true",
                   help="After review, commit tasks via task-batch-create.")
    p.add_argument("--dry-run", action="store_true",
                   help="With --commit: show what would be created without writing to the DB.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip interactive confirmation before committing.")
    p.add_argument("--no-id-preview", action="store_true",
                   help="Skip the dry-run ID pre-allocation (faster, but depends_on: [previous] won't resolve to real IDs).")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    brief = _load_brief(args.brief)
    tasks = _expand_tasks(brief)

    # Pre-allocate IDs so "previous" deps show real task IDs in the review
    if not args.no_id_preview:
        tasks = _allocate_ids(tasks)

    # Print human-readable summary for review
    _print_draft(tasks, brief)

    # Build the YAML pack
    pack = _build_pack(tasks)
    pack_yaml = _dump_yaml(pack)

    # Write to output file if requested
    if args.output:
        Path(args.output).write_text(pack_yaml)
        print(f"Draft saved to: {args.output}")
    else:
        print("--- draft YAML (pass --output FILE to save) ---")
        print(pack_yaml)
        print("--- end draft ---")

    if not args.commit:
        print("Review complete. Use --commit to create tasks, or edit the YAML first.")
        return

    if args.dry_run:
        print("\n[dry-run] Previewing task-batch-create (no DB writes):")
        _commit_pack(tasks, dry_run=True)
        return

    if not args.yes:
        if not _confirm(f"\nCreate {len(tasks)} task(s)? [y/N] "):
            print("Aborted.")
            return

    print("\nCreating tasks...")
    _commit_pack(tasks, dry_run=False)


if __name__ == "__main__":
    main()
