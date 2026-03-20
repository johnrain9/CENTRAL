#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$repo_root/scripts/central_task_db.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
bundle_dir="$tmpdir/generated"

python3 "$cli" init --db-path "$db_path" >/dev/null
python3 "$cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root "$repo_root" --display-name CENTRAL >/dev/null

make_task_payload() {
  local task_id="$1"
  local title="$2"
  local priority="$3"
  local metadata_json="$4"
  cat >"$tmpdir/$task_id.json" <<JSON
{
  "task_id": "$task_id",
  "title": "$title",
  "summary": "Audit-aware reporting smoke payload for $task_id",
  "objective_md": "Exercise audit-aware planner reporting.",
  "context_md": "Temporary DB only.",
  "scope_md": "Reporting smoke only.",
  "deliverables_md": "Linked audit state appears in planner views.",
  "acceptance_md": "Audit linkage is visible without metadata inspection.",
  "testing_md": "Run the audit reporting smoke test.",
  "dispatch_md": "Dispatch locally only.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": $priority,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "$repo_root",
  "approval_required": false,
  "metadata": $metadata_json,
  "execution": {
    "task_kind": "mutating",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "additional_writable_dirs": [],
    "timeout_seconds": 60,
    "metadata": {}
  },
  "dependencies": []
}
JSON
}

make_task_payload "CENTRAL-OPS-9300" "Accepted audit parent" 1 '{"test_case":"audit-reporting-accepted"}'
make_task_payload "CENTRAL-OPS-9301" "Failed audit parent" 2 '{"test_case":"audit-reporting-failed"}'
make_task_payload "CENTRAL-OPS-9302" "Ready audit parent" 3 '{"test_case":"audit-reporting-ready"}'

python3 "$cli" task-create --db-path "$db_path" --input "$tmpdir/CENTRAL-OPS-9300.json" >/dev/null
python3 "$cli" task-create --db-path "$db_path" --input "$tmpdir/CENTRAL-OPS-9301.json" >/dev/null
python3 "$cli" task-create --db-path "$db_path" --input "$tmpdir/CENTRAL-OPS-9302.json" >/dev/null

python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-a --task-id CENTRAL-OPS-9300 >/dev/null
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9300 --status done --worker-id worker-a --notes "implementation ready for audit" >/dev/null
python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-audit --task-id CENTRAL-OPS-9300-AUDIT >/dev/null
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9300-AUDIT --status done --worker-id worker-audit --notes "audit accepted implementation" >/dev/null

python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-b --task-id CENTRAL-OPS-9301 >/dev/null
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9301 --status done --worker-id worker-b --notes "implementation ready for audit" >/dev/null
failed_audit_version="$(python3 "$cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9301-AUDIT --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
python3 "$cli" task-reconcile --db-path "$db_path" --task-id CENTRAL-OPS-9301-AUDIT --expected-version "$failed_audit_version" --outcome failed --summary "Audit found a regression" --notes "Planner follow-up required" >/dev/null

cat >"$tmpdir/CENTRAL-OPS-9303.json" <<JSON
{
  "task_id": "CENTRAL-OPS-9303",
  "title": "Rework after failed audit",
  "summary": "Follow-up work spawned from a failed audit",
  "objective_md": "Fix the regression identified by the failed audit.",
  "context_md": "Temporary DB only.",
  "scope_md": "Reporting smoke only.",
  "deliverables_md": "One explicit rework task linked to the failed audit.",
  "acceptance_md": "Audit views show this task as failed-audit rework.",
  "testing_md": "Run the audit reporting smoke test.",
  "dispatch_md": "Dispatch locally only.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": 4,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "$repo_root",
  "approval_required": false,
  "metadata": {
    "audit_required": false,
    "relationship_kind": "rework",
    "parent_task_id": "CENTRAL-OPS-9301",
    "audit_task_id": "CENTRAL-OPS-9301-AUDIT",
    "test_case": "audit-reporting-rework"
  },
  "execution": {
    "task_kind": "mutating",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "additional_writable_dirs": [],
    "timeout_seconds": 60,
    "metadata": {}
  },
  "dependencies": ["CENTRAL-OPS-9301-AUDIT"]
}
JSON
python3 "$cli" task-create --db-path "$db_path" --input "$tmpdir/CENTRAL-OPS-9303.json" >/dev/null

python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-c --task-id CENTRAL-OPS-9302 >/dev/null
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9302 --status done --worker-id worker-c --notes "implementation ready for audit" >/dev/null

python3 "$cli" view-summary --db-path "$db_path" --json >"$tmpdir/summary.json"
python3 "$cli" view-eligible --db-path "$db_path" --json >"$tmpdir/eligible.json"
python3 "$cli" view-audits --db-path "$db_path" --json >"$tmpdir/audits.json"
python3 "$cli" view-audits --db-path "$db_path" --section ready --json >"$tmpdir/audits-ready.json"
python3 "$cli" view-planner-panel --db-path "$db_path" --json >"$tmpdir/planner-panel.json"
python3 "$cli" export-markdown-bundle --db-path "$db_path" --output-dir "$bundle_dir" --json >"$tmpdir/export.json"

python3 - "$tmpdir/summary.json" "$tmpdir/eligible.json" "$tmpdir/audits.json" "$tmpdir/audits-ready.json" "$tmpdir/planner-panel.json" "$tmpdir/export.json" "$bundle_dir/audit_queue.md" <<'PY'
import json
import pathlib
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
eligible = json.load(open(sys.argv[2], encoding="utf-8"))
audits = json.load(open(sys.argv[3], encoding="utf-8"))
ready = json.load(open(sys.argv[4], encoding="utf-8"))
planner_panel = json.load(open(sys.argv[5], encoding="utf-8"))
export_payload = json.load(open(sys.argv[6], encoding="utf-8"))
audit_md = pathlib.Path(sys.argv[7]).read_text(encoding="utf-8")

assert summary["audit"]["linked_pair_count"] == 3, summary
assert summary["audit"]["awaiting_audit_count"] == 1, summary
assert summary["audit"]["ready_audit_count"] == 1, summary
assert summary["audit"]["accepted_audit_count"] == 1, summary
assert summary["audit"]["failed_audit_count"] == 1, summary
assert summary["audit"]["rework_spawned_count"] == 1, summary

assert eligible[0]["task_id"] == "CENTRAL-OPS-9302-AUDIT", eligible
assert eligible[0]["task_type"] == "audit", eligible
assert eligible[0]["audit_link"] == "CENTRAL-OPS-9302", eligible

assert audits["summary"]["linked_pair_count"] == 3, audits
failed_pair = next(row for row in audits["completed_audits"] if row["audit_task_id"] == "CENTRAL-OPS-9301-AUDIT")
assert failed_pair["audit_verdict"] == "failed", failed_pair
assert failed_pair["parent_planner_status"] == "failed", failed_pair
assert failed_pair["rework_task_ids"] == ["CENTRAL-OPS-9303"], failed_pair
assert ready == [row for row in audits["ready_audits"]], ready
assert ready[0]["audit_task_id"] == "CENTRAL-OPS-9302-AUDIT", ready

assert planner_panel["summary"]["ready_audit_count"] == 1, planner_panel
assert planner_panel["summary"]["failed_audit_count"] == 1, planner_panel
assert planner_panel["summary"]["rework_spawned_count"] == 1, planner_panel
assert planner_panel["ready_audits"][0]["audit_task_id"] == "CENTRAL-OPS-9302-AUDIT", planner_panel

assert export_payload["audit_path"].endswith("audit_queue.md"), export_payload
assert "CENTRAL-OPS-9302-AUDIT" in audit_md, audit_md
assert "CENTRAL-OPS-9303" in audit_md, audit_md
assert "Failed audits: 1" in audit_md, audit_md
PY

echo "audit reporting smoke passed"
