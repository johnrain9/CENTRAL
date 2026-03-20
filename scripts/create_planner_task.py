#!/usr/bin/env python3
"""AI-facing helper for creating CENTRAL planner tasks without hand-writing JSON.

The canonical task schema stays rich on purpose. This helper reduces repetitive
task creation work by keeping high-signal fields explicit, defaulting repetitive
sections through presets, and making the resulting canonical payload easy to
preview before it is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import central_task_db as task_db


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent

SECTION_NAMES = [
    "objective",
    "context",
    "scope",
    "deliverables",
    "acceptance",
    "testing",
    "dispatch",
    "closeout",
    "reconciliation",
]

SECTION_FIELD_MAP = {name: f"{name}_md" for name in SECTION_NAMES}

PRESETS: dict[str, dict[str, Any]] = {
    "implementation": {
        "task_type": "implementation",
        "task_kind": "mutating",
        "sandbox_mode": "workspace-write",
        "approval_policy": "never",
        "timeout_seconds": 1800,
        "audit_mode": "full",
        "dispatch": (
            "Implement in the target repo only. Use the execution settings in this task as the runtime contract."
        ),
        "closeout": (
            "Report the final change, validation performed, artifacts produced, and any remaining risks or follow-ups."
        ),
        "reconciliation_with_audit": (
            "Normal implementation task with paired audit. Reconcile to `awaiting_audit` when execution evidence is complete."
        ),
        "reconciliation_without_audit": (
            "Planner may reconcile directly to `done` once evidence satisfies acceptance."
        ),
    },
    "planning": {
        "task_type": "planning",
        "task_kind": "read_only",
        "sandbox_mode": "workspace-write",
        "approval_policy": "never",
        "timeout_seconds": 1800,
        "audit_mode": "none",
        "dispatch": "Plan in CENTRAL only. Do not broaden scope beyond the stated objective and constraints.",
        "closeout": "Return the plan, assumptions, open questions, and any recommended follow-up tasks.",
        "reconciliation_with_audit": "Planner task; paired audits are normally disabled for this preset.",
        "reconciliation_without_audit": "Planner may close directly when the planning artifact is complete.",
    },
    "research": {
        "task_type": "research",
        "task_kind": "read_only",
        "sandbox_mode": "workspace-write",
        "approval_policy": "never",
        "timeout_seconds": 1800,
        "audit_mode": "none",
        "dispatch": "Research within the target repo and explicitly cited sources only.",
        "closeout": "Return findings, evidence, gaps, and any follow-up actions needed.",
        "reconciliation_with_audit": "Research task; paired audits are normally disabled for this preset.",
        "reconciliation_without_audit": "Planner may close directly when findings are sufficiently evidenced.",
    },
}

HELP_EPILOG = """\
Required content fields:
  --task-id, --title, --objective, --context/--context-item, --scope/--scope-item,
  --deliverables/--deliverable, --acceptance/--acceptance-item, --testing/--test

Optional with defaults:
  --summary defaults to --title
  --dispatch, --closeout, and --reconciliation default from --preset
  execution settings default from --preset unless overridden explicitly
  --initiative is optional but recommended for tasks that belong to a larger feature,
  workstream, or auditable bundle

Audit semantics:
  Paired audits are only auto-created for planner-owned implementation tasks.
  Use --audit-mode full|light|none for explicit intent.
    full  = default for non-trivial implementation work; use when the change
            can be wrong even if tests pass and you want an independent audit
            of requirements, real behavior, and whole-system fit.
    light = bounded audit for lower-risk implementation work such as focused
            tests, observability, or narrow slices where we still want
            independent verification but not a broad feature-level investigation.
    none  = only for trivial or explicitly exempt work.
  The legacy value `required` is still accepted and is treated as `full`.
  --no-audit remains a compatibility alias for --audit-mode none.

Backfill semantics:
  Use --backfill when the implementation already landed before canonical task creation.
  Backfill tasks are created directly in `awaiting_audit`, stay non-dispatchable as implementation work,
  and create an immediately eligible paired audit. Provide one or more --landed-ref values so the audit
  can inspect the actual landed change rather than a pretend future implementation run.

Examples:
  python3 scripts/create_planner_task.py --dry-run --preset implementation \\
    --task-id CENTRAL-OPS-35 --title "Improve task creation UX" \\
    --objective "Reduce repetitive boilerplate for AI planners." \\
    --context-item "The canonical schema must remain rich." \\
    --scope-item "Change CENTRAL task creation tooling only." \\
    --deliverable "Improved helper UX" --deliverable "Focused smokes" \\
    --acceptance-item "AI can create rich tasks with less repetition." \\
    --test "python3 -m unittest tests.test_create_planner_task"
"""


def read_text_arg(value: str | None) -> str:
    if value is None:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).expanduser().read_text(encoding="utf-8").strip()
    return value.strip()


def parse_key_value(items: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"invalid key=value pair: {item!r}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise SystemExit(f"invalid metadata key in {item!r}")
        try:
            payload[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            payload[key] = raw_value
    return payload


def render_markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def resolve_section(args: argparse.Namespace, name: str, *, default: str | None = None, required: bool = False) -> str:
    text_value = getattr(args, name)
    raw_items = getattr(args, f"{name}_items", [])
    list_values = [item.strip() for item in raw_items if item.strip()]
    if text_value and list_values:
        raise SystemExit(f"use either --{name} or the list-style flags for {name}, not both")
    if text_value:
        rendered = read_text_arg(text_value)
    elif list_values:
        rendered = render_markdown_list(list_values)
    else:
        rendered = (default or "").strip()
    if required and not rendered:
        raise SystemExit(
            f"missing required content for {name}; use --{name} or the list-style flags documented in --help"
        )
    return rendered


def resolve_preset(args: argparse.Namespace) -> dict[str, Any]:
    return dict(PRESETS[args.preset])


def resolve_audit_mode(args: argparse.Namespace, preset: dict[str, Any]) -> str:
    if args.no_audit:
        return "none"
    raw_mode = str(args.audit_mode or preset["audit_mode"]).strip().lower()
    if raw_mode == "required":
        return "full"
    if raw_mode not in {"full", "light", "none"}:
        raise SystemExit(f"unsupported audit mode: {raw_mode}")
    return raw_mode


def resolve_summary(args: argparse.Namespace) -> str:
    return (args.summary or args.title).strip()


def resolve_section_defaults(args: argparse.Namespace, preset: dict[str, Any], audit_mode: str) -> dict[str, str]:
    if args.backfill:
        return {
            "objective": "",
            "context": "",
            "scope": "",
            "deliverables": "",
            "acceptance": "",
            "testing": "",
            "dispatch": (
                "Do not dispatch implementation work from this task. The change already landed; use this task "
                "to preserve canonical history and drive the paired audit only."
            ),
            "closeout": (
                "Record landed change references, why the work is being backfilled, and any evidence the audit "
                "should inspect. Do not imply a new implementation dispatch occurred."
            ),
            "reconciliation": (
                "Backfill task: create it directly in `awaiting_audit` with a paired audit. The audit should "
                "verify the landed change and confirm the lifecycle remains truthful."
            ),
        }
    reconciliation_key = "reconciliation_with_audit" if audit_mode in {"full", "light"} else "reconciliation_without_audit"
    defaults = {
        "dispatch": str(preset.get("dispatch") or ""),
        "closeout": str(preset.get("closeout") or ""),
        "reconciliation": str(preset.get(reconciliation_key) or ""),
    }
    return {
        "objective": "",
        "context": "",
        "scope": "",
        "deliverables": "",
        "acceptance": "",
        "testing": "",
        **defaults,
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    preset = resolve_preset(args)
    audit_mode = resolve_audit_mode(args, preset)

    metadata = parse_key_value(args.metadata)
    execution_metadata = parse_key_value(args.execution_metadata)
    repo_root = str((Path(args.target_repo_root).expanduser().resolve() if args.target_repo_root else REPO_ROOT))
    repo_id = args.target_repo_id
    defaults = resolve_section_defaults(args, preset, audit_mode)

    task_type = args.task_type or str(preset["task_type"])
    task_kind = args.task_kind or str(preset["task_kind"])
    sandbox_mode = args.sandbox_mode or str(preset["sandbox_mode"])
    approval_policy = args.approval_policy or str(preset["approval_policy"])
    timeout_seconds = args.timeout_seconds if args.timeout_seconds is not None else int(preset["timeout_seconds"])
    planner_status = "awaiting_audit" if args.backfill and args.planner_status == "todo" else args.planner_status

    if args.backfill:
        if task_type != "implementation":
            raise SystemExit("--backfill is only supported for implementation tasks")
        if audit_mode not in {"full", "light"}:
            raise SystemExit("--backfill requires a paired audit; use the normal workflow for non-audited tasks")
        if planner_status != "awaiting_audit":
            raise SystemExit("--backfill tasks must start in planner_status awaiting_audit")

    audit_required = task_type == "implementation" and audit_mode in {"full", "light"}
    metadata["audit_required"] = audit_required
    metadata["audit_policy"] = audit_mode
    if args.backfill:
        landed_refs = [value.strip() for value in args.landed_ref if value.strip()]
        if not landed_refs:
            raise SystemExit("--backfill requires at least one --landed-ref")
        backfill_reason = read_text_arg(args.backfill_reason) if args.backfill_reason else ""
        metadata["workflow_kind"] = "backfill"
        metadata["backfill_landed_refs"] = landed_refs
        if backfill_reason:
            metadata["backfill_reason"] = backfill_reason
        metadata["audit_verdict"] = "pending"
        metadata["closeout"] = {
            "outcome": "awaiting_audit",
            "summary": "Backfilled after the implementation already landed; waiting for independent audit.",
            "notes": backfill_reason or f"Landed refs: {', '.join(landed_refs)}",
            "tests": None,
            "reconciled_at": "created_via_backfill_helper",
            "actor_id": args.actor_id,
        }
    if audit_required:
        metadata.setdefault("fixup_threshold", "bounded_only")

    section_values = {
        name: resolve_section(args, name, default=defaults.get(name), required=name in {
            "objective",
            "context",
            "scope",
            "deliverables",
            "acceptance",
            "testing",
        })
        for name in SECTION_NAMES
    }

    payload = {
        "task_id": args.task_id,
        "title": args.title,
        "summary": resolve_summary(args),
        **{SECTION_FIELD_MAP[name]: value for name, value in section_values.items()},
        "planner_status": planner_status,
        "priority": args.priority,
        "initiative": args.initiative,
        "task_type": task_type,
        "planner_owner": args.planner_owner,
        "worker_owner": args.worker_owner,
        "target_repo_id": repo_id,
        "target_repo_root": repo_root,
        "target_repo_display_name": repo_id,
        "approval_required": args.approval_required,
        "metadata": metadata,
        "execution": {
            "task_kind": task_kind,
            "sandbox_mode": sandbox_mode,
            "approval_policy": approval_policy,
            "additional_writable_dirs": args.additional_writable_dir,
            "timeout_seconds": timeout_seconds,
            "metadata": execution_metadata,
        },
        "dependencies": args.dependency,
    }
    if args.backfill:
        audit_context_lines = [
            f"Parent task: `{args.task_id}`.",
            "",
            "This is a backfilled implementation task. Audit the code that already landed rather than expecting a new implementation dispatch.",
            "",
            "Landed change references:",
            *[f"- {ref}" for ref in metadata["backfill_landed_refs"]],
        ]
        if args.audit_focus:
            audit_context_lines.extend(["", "Focused audit expectations:", *[f"- {item}" for item in args.audit_focus]])
        payload["audit"] = {
            "summary": f"Audit the already-landed change for {args.task_id}.",
            "context_md": "\n".join(audit_context_lines),
            "dispatch_md": f"Audit immediately. `{args.task_id}` is a backfill task that already landed and is already in `awaiting_audit`.",
            "acceptance_md": (
                "Verify the landed change against the canonical task requirements, referenced change set, and overall system behavior."
            ),
            "metadata": {"audit_policy": audit_mode, "fixup_threshold": "bounded_only"},
        }
    elif audit_mode == "light":
        payload["audit"] = {
            "summary": (
                f"Light audit for {args.task_id}: verify the narrow intended outcome without broad feature re-investigation."
            ),
            "objective_md": (
                f"Perform a bounded audit of `{args.task_id}` focused on the stated acceptance criteria, "
                "real observed behavior, and obvious local-vs-system mismatches."
            ),
            "scope_md": (
                "Keep the audit narrow. Validate the intended outcome directly and avoid broad repo or whole-feature "
                "exploration unless the initial evidence shows a likely mismatch."
            ),
            "deliverables_md": (
                "Record a concise audit verdict with decisive evidence, note any bounded fixups, and call out any "
                "reason the task should escalate to broader planner review."
            ),
            "acceptance_md": (
                "Confirm the bounded change works as intended in reality and does not obviously violate nearby system expectations."
            ),
            "testing_md": (
                "Use one or a few decisive validation paths. Prefer direct evidence over broad redundant investigation."
            ),
            "closeout_md": (
                "Record verdict, concise evidence, and any bounded fixups. Escalate instead of broadening the audit beyond its intended scope."
            ),
            "metadata": {"audit_policy": "light", "fixup_threshold": "bounded_only"},
        }
    elif audit_mode == "full":
        payload["audit"] = {
            "metadata": {"audit_policy": "full", "fixup_threshold": "bounded_only"},
        }
    return payload


def build_preview_graph(payload: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {"parent": payload}
    metadata = dict(payload.get("metadata") or {})
    if task_db.task_requires_audit(
        task_type=str(payload.get("task_type") or ""),
        source_kind="planner",
        metadata=metadata,
    ):
        preview["audit"] = task_db.build_audit_task_payload(payload, payload.get("audit"))
    else:
        preview["audit"] = None
    return preview


def add_list_argument(parser: argparse.ArgumentParser, name: str, singular: str, help_text: str) -> None:
    parser.add_argument(
        f"--{singular}",
        dest=f"{name}_items",
        action="append",
        default=[],
        help=help_text,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a planner-owned CENTRAL task without hand-writing canonical JSON.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db-path", help="Override the CENTRAL SQLite DB path. Defaults to the repo DB or CENTRAL_TASK_DB_PATH.")
    parser.add_argument("--actor-id", default="planner/coordinator", help="Planner identity recorded in task creation events.")
    parser.add_argument("--json", action="store_true", help="Print the created parent task card as JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Print the parent canonical payload without writing to the DB.")
    parser.add_argument("--preview-graph", action="store_true", help="Print the derived parent plus auto-audit payloads without writing to the DB.")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="implementation", help="Creation preset that supplies defaults for task type, execution settings, and repetitive narrative sections.")
    parser.add_argument("--task-id", required=True, help="Stable canonical task identifier, for example CENTRAL-OPS-35.")
    parser.add_argument("--title", required=True, help="Short human-readable title for the task.")
    parser.add_argument("--summary", help="Compact list-view summary. Optional; defaults to --title.")
    parser.add_argument("--objective", help="Primary intended outcome. Accepts literal text or @path to load from file.")
    parser.add_argument("--context", help="Background and motivation. Accepts text or @path. May be replaced by repeated --context-item.")
    parser.add_argument("--scope", help="Boundary conditions and explicit in/out-of-scope guidance. Accepts text or @path. May be replaced by repeated --scope-item.")
    parser.add_argument("--deliverables", help="Concrete outputs expected from the worker. Accepts text or @path. May be replaced by repeated --deliverable.")
    parser.add_argument("--acceptance", help="Conditions that must be true for the task to be correct. Accepts text or @path. May be replaced by repeated --acceptance-item.")
    parser.add_argument("--testing", help="Validation commands or testing expectations. Accepts text or @path. May be replaced by repeated --test.")
    parser.add_argument("--dispatch", help="Worker execution guidance. Optional; defaults from --preset. Accepts text or @path. May be replaced by repeated --dispatch-item.")
    parser.add_argument("--closeout", help="What completion must report back. Optional; defaults from --preset. Accepts text or @path. May be replaced by repeated --closeout-item.")
    parser.add_argument("--reconciliation", help="Planner-side post-execution handling. Optional; defaults from --preset. Accepts text or @path. May be replaced by repeated --reconciliation-item.")
    add_list_argument(parser, "context", "context-item", "Add one context bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "scope", "scope-item", "Add one scope bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "deliverables", "deliverable", "Add one deliverable bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "acceptance", "acceptance-item", "Add one acceptance bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "testing", "test", "Add one testing or validation bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "dispatch", "dispatch-item", "Add one dispatch bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "closeout", "closeout-item", "Add one closeout bullet. Repeat for multiple bullets.")
    add_list_argument(parser, "reconciliation", "reconciliation-item", "Add one reconciliation bullet. Repeat for multiple bullets.")
    parser.add_argument("--planner-status", default="todo", choices=sorted(task_db.PLANNER_STATUSES), help="Initial planner lifecycle state for the task.")
    parser.add_argument("--priority", type=int, default=100, help="Numeric scheduling priority. Lower numbers are dispatched first.")
    parser.add_argument("--initiative", help="Optional feature/workstream tag for grouping related tasks and later feature-level audits (for example: planner-status-ui or dispatcher-runtime-reliability).")
    parser.add_argument("--task-type", help="Canonical task category. Optional; defaults from --preset.")
    parser.add_argument("--planner-owner", default="planner/coordinator", help="Planner identity responsible for sequencing and reconciliation.")
    parser.add_argument("--worker-owner", help="Optional intended worker identity. Leave unset for general dispatch.")
    parser.add_argument("--target-repo-id", default="CENTRAL", help="Canonical repo identifier where the work belongs.")
    parser.add_argument("--target-repo-root", default=str(REPO_ROOT), help="Filesystem root for the target repo. Defaults to the current CENTRAL checkout.")
    parser.add_argument("--approval-required", action="store_true", help="Mark the task as requiring approval before execution.")
    parser.add_argument("--dependency", "--depends-on", dest="dependency", action="append", default=[], help="Add a hard dependency task ID that must reach `done`. Repeat for multiple dependencies.")
    parser.add_argument("--metadata", action="append", default=[], help="Task metadata key=value pair. Value may be JSON; repeat for multiple entries.")
    parser.add_argument(
        "--audit-mode",
        help=(
            "Paired-audit policy for implementation tasks. "
            "`full` = independent audit for non-trivial or higher-risk work. "
            "`light` = narrower audit for bounded lower-risk work. "
            "`none` = no paired audit. "
            "Legacy `required` is accepted as an alias for `full`."
        ),
    )
    parser.add_argument("--no-audit", action="store_true", help="Compatibility alias for --audit-mode none.")
    parser.add_argument("--backfill", action="store_true", help="Create an already-landed implementation task directly in `awaiting_audit` with an immediately eligible paired audit.")
    parser.add_argument("--landed-ref", action="append", default=[], help="Commit, PR, diff, or artifact reference for a backfilled change. Repeat for multiple references.")
    parser.add_argument("--backfill-reason", help="Why the task is being backfilled now. Accepts text or @path.")
    parser.add_argument("--audit-focus", action="append", default=[], help="Focused expectation the paired audit should verify for a backfilled task. Repeat for multiple bullets.")
    parser.add_argument("--task-kind", help="Execution kind stored in execution settings. Optional; defaults from --preset.")
    parser.add_argument("--sandbox-mode", help="Execution sandbox mode. Optional; defaults from --preset.")
    parser.add_argument("--approval-policy", help="Execution approval policy. Optional; defaults from --preset.")
    parser.add_argument("--additional-writable-dir", action="append", default=[], help="Extra writable directory for execution. Repeat for multiple paths.")
    parser.add_argument("--timeout-seconds", type=int, help="Worker execution timeout in seconds. Optional; defaults from --preset.")
    parser.add_argument("--execution-metadata", action="append", default=[], help="Execution metadata key=value pair. Value may be JSON; repeat for multiple entries.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = build_payload(args)
    if args.preview_graph:
        print(task_db.json_dumps(build_preview_graph(payload)))
        return 0
    if args.dry_run:
        print(task_db.json_dumps(payload))
        return 0
    conn, _ = task_db.open_initialized_connection(args.db_path)
    try:
        with conn:
            snapshot = task_db.create_task_graph(conn, payload, actor_kind="planner", actor_id=args.actor_id)
    finally:
        conn.close()
    card = task_db.render_task_card(snapshot)
    print(task_db.json_dumps(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
