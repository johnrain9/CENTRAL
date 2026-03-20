#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
db_cli="$repo_root/scripts/central_task_db.py"
create_cli="$repo_root/scripts/create_planner_task.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
created_json="$tmpdir/created.json"
audit_json="$tmpdir/audit.json"
eligible_json="$tmpdir/eligible.json"
audits_ready_json="$tmpdir/audits-ready.json"
claim_json="$tmpdir/claim.json"
parent_final_json="$tmpdir/parent-final.json"
audit_final_json="$tmpdir/audit-final.json"

python3 "$db_cli" init --db-path "$db_path" >/dev/null
python3 "$db_cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root "$repo_root" --display-name CENTRAL >/dev/null

python3 "$create_cli" \
  --db-path "$db_path" \
  --task-id CENTRAL-OPS-9400 \
  --title "Backfill smoke task" \
  --objective "Create an audit-ready backfill task for already-landed work." \
  --context-item "The implementation merged before canonical task creation." \
  --scope-item "Temporary DB only." \
  --deliverable "Backfilled implementation task." \
  --deliverable "Immediately eligible paired audit." \
  --acceptance-item "The task enters awaiting_audit without fake implementation dispatch." \
  --test "bash tests/test_central_backfill_flow.sh" \
  --backfill \
  --landed-ref "commit:deadbeef" \
  --landed-ref "pr:https://example.invalid/backfill/9400" \
  --backfill-reason "Fast-path work landed before canonical task creation." \
  --audit-focus "Verify the landed diff matches the recorded scope." \
  --json >"$created_json"

python3 "$db_cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9400-AUDIT --json >"$audit_json"
python3 "$db_cli" view-eligible --db-path "$db_path" --json >"$eligible_json"
python3 "$db_cli" view-audits --db-path "$db_path" --section ready --json >"$audits_ready_json"
python3 "$db_cli" runtime-claim --db-path "$db_path" --worker-id worker-audit --json >"$claim_json"
python3 "$db_cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9400-AUDIT --status running --worker-id worker-audit >/dev/null
python3 "$db_cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9400-AUDIT --status done --worker-id worker-audit --notes "Audit accepted the backfilled change" >/dev/null
python3 "$db_cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9400 --json >"$parent_final_json"
python3 "$db_cli" task-show --db-path "$db_path" --task-id CENTRAL-OPS-9400-AUDIT --json >"$audit_final_json"

python3 - "$created_json" "$audit_json" "$eligible_json" "$audits_ready_json" "$claim_json" "$parent_final_json" "$audit_final_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    created = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    audit = json.load(handle)
with open(sys.argv[3], encoding="utf-8") as handle:
    eligible = json.load(handle)
with open(sys.argv[4], encoding="utf-8") as handle:
    audits_ready = json.load(handle)
with open(sys.argv[5], encoding="utf-8") as handle:
    claim = json.load(handle)
with open(sys.argv[6], encoding="utf-8") as handle:
    parent_final = json.load(handle)
with open(sys.argv[7], encoding="utf-8") as handle:
    audit_final = json.load(handle)

assert created["planner_status"] == "awaiting_audit", created
assert created["runtime_status"] is None, created
assert created["metadata"]["workflow_kind"] == "backfill", created
assert created["metadata"]["backfill_landed_refs"] == [
    "commit:deadbeef",
    "pr:https://example.invalid/backfill/9400",
], created
assert created["metadata"]["child_audit_task_id"] == "CENTRAL-OPS-9400-AUDIT", created
assert created["metadata"]["audit_verdict"] == "pending", created
assert created["metadata"]["closeout"]["outcome"] == "awaiting_audit", created

assert audit["planner_status"] == "todo", audit
assert audit["metadata"]["parent_task_id"] == "CENTRAL-OPS-9400", audit
assert audit["dependencies"][0]["depends_on_task_id"] == "CENTRAL-OPS-9400", audit

assert [row["task_id"] for row in eligible] == ["CENTRAL-OPS-9400-AUDIT"], eligible
assert len(audits_ready) == 1, audits_ready
assert audits_ready[0]["parent_task_id"] == "CENTRAL-OPS-9400", audits_ready
assert audits_ready[0]["audit_task_id"] == "CENTRAL-OPS-9400-AUDIT", audits_ready
assert audits_ready[0]["audit_ready"] == "yes", audits_ready

assert claim["task_id"] == "CENTRAL-OPS-9400-AUDIT", claim
assert claim["runtime_status"] == "claimed", claim

assert audit_final["planner_status"] == "done", audit_final
assert audit_final["runtime_status"] == "done", audit_final
assert parent_final["planner_status"] == "done", parent_final
assert parent_final["runtime_status"] is None, parent_final
assert parent_final["metadata"]["audit_verdict"] == "accepted", parent_final
assert parent_final["metadata"]["audit_task_id"] == "CENTRAL-OPS-9400-AUDIT", parent_final
PY

echo "backfill flow smoke passed"
