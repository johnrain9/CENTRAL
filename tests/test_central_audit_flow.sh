#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$repo_root/scripts/central_task_db.py"
runtime_cli="$repo_root/scripts/central_runtime.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
state_dir="$tmpdir/runtime_state"
parent_payload="$tmpdir/parent.json"
normal_payload="$tmpdir/normal.json"
created_parent_json="$tmpdir/created-parent.json"
created_audit_json="$tmpdir/created-audit.json"
created_normal_json="$tmpdir/created-normal.json"
eligible_before_json="$tmpdir/eligible-before.json"
status_before_json="$tmpdir/status-before.json"
eligible_during_json="$tmpdir/eligible-during.json"
status_during_json="$tmpdir/status-during.json"
eligible_after_json="$tmpdir/eligible-after.json"
status_after_json="$tmpdir/status-after.json"
parent_after_impl_json="$tmpdir/parent-after-impl.json"
parent_final_json="$tmpdir/parent-final.json"
audit_final_json="$tmpdir/audit-final.json"
claim_parent_json="$tmpdir/claim-parent.json"
claim_audit_json="$tmpdir/claim-audit.json"

cat >"$parent_payload" <<JSON
{
  "task_id": "CENTRAL-OPS-9200",
  "title": "Audit flow smoke task",
  "summary": "Validate coupled audit lifecycle behavior",
  "objective_md": "Create an implementation task that requires an audit.",
  "context_md": "Used only by the audit flow smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "An implementation task and a linked audit task.",
  "acceptance_md": "Audit task is created immediately and closes the parent when accepted.",
  "testing_md": "Run the audit flow smoke test.",
  "dispatch_md": "Dispatch locally only.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": 1,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "$repo_root",
  "approval_required": false,
  "metadata": {
    "test_case": "audit-flow"
  },
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

cat >"$normal_payload" <<JSON
{
  "task_id": "CENTRAL-OPS-9201",
  "title": "Ordinary competing task",
  "summary": "Remain eligible while another task is running.",
  "objective_md": "Provide ordinary implementation work that can lose to a newly eligible audit at claim time.",
  "context_md": "Used only by the audit preemption smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "One ordinary eligible task.",
  "acceptance_md": "The task stays eligible until claimed.",
  "testing_md": "Run the audit flow smoke test.",
  "dispatch_md": "Dispatch locally only.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": 5,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "$repo_root",
  "approval_required": false,
  "metadata": {
    "test_case": "audit-flow-competing-normal",
    "audit_required": false
  },
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

python3 "$cli" init --db-path "$db_path" >/dev/null
python3 "$cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root "$repo_root" --display-name CENTRAL >/dev/null
python3 "$cli" task-create --db-path "$db_path" --input "$parent_payload" --json >"$created_parent_json"
python3 "$cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9200-AUDIT --json >"$created_audit_json"
python3 "$cli" task-create --db-path "$db_path" --input "$normal_payload" --json >"$created_normal_json"
python3 "$cli" view-eligible --db-path "$db_path" --json >"$eligible_before_json"
python3 "$runtime_cli" status --db-path "$db_path" --state-dir "$state_dir" --json >"$status_before_json"
python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-impl --task-id CENTRAL-OPS-9200 --json >"$claim_parent_json"
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9200 --status running --worker-id worker-impl >/dev/null
python3 "$cli" view-eligible --db-path "$db_path" --json >"$eligible_during_json"
python3 "$runtime_cli" status --db-path "$db_path" --state-dir "$state_dir" --json >"$status_during_json"
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9200 --status done --worker-id worker-impl --notes "Implementation completed and ready for audit" >/dev/null
python3 "$cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9200 --json >"$parent_after_impl_json"
python3 "$cli" view-eligible --db-path "$db_path" --json >"$eligible_after_json"
python3 "$runtime_cli" status --db-path "$db_path" --state-dir "$state_dir" --json >"$status_after_json"
python3 "$cli" runtime-claim --db-path "$db_path" --worker-id worker-audit --json >"$claim_audit_json"
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9200-AUDIT --status running --worker-id worker-audit >/dev/null
python3 "$cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9200-AUDIT --status done --worker-id worker-audit --notes "Audit accepted the implementation" >/dev/null
python3 "$cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9200 --json >"$parent_final_json"
python3 "$cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9200-AUDIT --json >"$audit_final_json"

python3 - "$created_parent_json" "$created_audit_json" "$created_normal_json" "$eligible_before_json" "$status_before_json" "$claim_parent_json" "$eligible_during_json" "$status_during_json" "$parent_after_impl_json" "$eligible_after_json" "$status_after_json" "$claim_audit_json" "$parent_final_json" "$audit_final_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    created_parent = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    created_audit = json.load(handle)
with open(sys.argv[3], encoding="utf-8") as handle:
    created_normal = json.load(handle)
with open(sys.argv[4], encoding="utf-8") as handle:
    eligible_before = json.load(handle)
with open(sys.argv[5], encoding="utf-8") as handle:
    status_before = json.load(handle)
with open(sys.argv[6], encoding="utf-8") as handle:
    claim_parent = json.load(handle)
with open(sys.argv[7], encoding="utf-8") as handle:
    eligible_during = json.load(handle)
with open(sys.argv[8], encoding="utf-8") as handle:
    status_during = json.load(handle)
with open(sys.argv[9], encoding="utf-8") as handle:
    parent_after_impl = json.load(handle)
with open(sys.argv[10], encoding="utf-8") as handle:
    eligible_after = json.load(handle)
with open(sys.argv[11], encoding="utf-8") as handle:
    status_after = json.load(handle)
with open(sys.argv[12], encoding="utf-8") as handle:
    claim_audit = json.load(handle)
with open(sys.argv[13], encoding="utf-8") as handle:
    parent_final = json.load(handle)
with open(sys.argv[14], encoding="utf-8") as handle:
    audit_final = json.load(handle)

assert created_parent["metadata"]["child_audit_task_id"] == "CENTRAL-OPS-9200-AUDIT", created_parent
assert created_parent["version"] == 2, created_parent
assert created_audit["metadata"]["parent_task_id"] == "CENTRAL-OPS-9200", created_audit
assert created_audit["metadata"]["relationship_kind"] == "audit", created_audit
assert created_audit["planner_status"] == "todo", created_audit
assert created_audit["dependencies"][0]["depends_on_task_id"] == "CENTRAL-OPS-9200", created_audit
assert created_normal["task_id"] == "CENTRAL-OPS-9201", created_normal

assert [row["task_id"] for row in eligible_before] == ["CENTRAL-OPS-9200", "CENTRAL-OPS-9201"], eligible_before
assert status_before["eligible_count"] == 2, status_before
assert status_before["next_claim_advisory_task_id"] == "CENTRAL-OPS-9200", status_before
assert status_before["claim_policy"] == "claim_time_fresh_audit_preferred", status_before
assert status_before["parked_count"] == 1, status_before
assert status_before["parked_reason_counts"] == {"dependency-blocked": 1}, status_before
assert status_before["parked_task_ids_sample"] == ["CENTRAL-OPS-9200-AUDIT"], status_before
assert claim_parent["runtime_status"] == "claimed", claim_parent
assert [row["task_id"] for row in eligible_during] == ["CENTRAL-OPS-9201"], eligible_during
assert status_during["eligible_count"] == 1, status_during
assert status_during["next_claim_advisory_task_id"] == "CENTRAL-OPS-9201", status_during

assert parent_after_impl["planner_status"] == "awaiting_audit", parent_after_impl
assert parent_after_impl["metadata"]["audit_verdict"] == "pending", parent_after_impl
assert parent_after_impl["runtime_status"] == "done", parent_after_impl
assert [row["task_id"] for row in eligible_after] == ["CENTRAL-OPS-9200-AUDIT", "CENTRAL-OPS-9201"], eligible_after
assert status_after["eligible_count"] == 2, status_after
assert status_after["next_claim_advisory_task_id"] == "CENTRAL-OPS-9200-AUDIT", status_after
assert status_after["claim_policy"] == "claim_time_fresh_audit_preferred", status_after
assert status_after["parked_count"] == 0, status_after
assert status_after["parked_reason_counts"] == {}, status_after
assert status_after["parked_task_ids_sample"] == [], status_after

assert claim_audit["task_id"] == "CENTRAL-OPS-9200-AUDIT", claim_audit
assert claim_audit["runtime_status"] == "claimed", claim_audit
assert audit_final["planner_status"] == "done", audit_final
assert audit_final["runtime_status"] == "done", audit_final
assert parent_final["planner_status"] == "done", parent_final
assert parent_final["metadata"]["audit_verdict"] == "accepted", parent_final
assert parent_final["metadata"]["audit_task_id"] == "CENTRAL-OPS-9200-AUDIT", parent_final
PY

echo "audit flow smoke passed"
