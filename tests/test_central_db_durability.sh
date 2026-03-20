#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$repo_root/scripts/central_task_db.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
durability_dir="$tmpdir/durability/central_db"
restored_db="$tmpdir/restored/central_tasks.db"
task_payload="$tmpdir/task.json"
update_payload="$tmpdir/update.json"
restored_task_json="$tmpdir/restored-task.json"
snapshot_list_json="$tmpdir/snapshot-list.json"

cat >"$task_payload" <<JSON
{
  "task_id": "CENTRAL-OPS-9000",
  "title": "Durability smoke task",
  "summary": "Baseline summary before snapshot",
  "objective_md": "Exercise snapshot publish and restore.",
  "context_md": "Used only by the durability smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "A recoverable post-update task record.",
  "acceptance_md": "Snapshot restore returns the updated task state.",
  "testing_md": "Run the durability smoke test.",
  "dispatch_md": "Do not dispatch.",
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
    "test_case": "durability",
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

cat >"$update_payload" <<'JSON'
{
  "summary": "Updated summary after publish",
  "planner_status": "in_progress",
  "metadata": {
    "test_case": "durability-updated"
  }
}
JSON

python3 "$cli" init --db-path "$db_path" >/dev/null
python3 "$cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root "$repo_root" --display-name CENTRAL >/dev/null
python3 "$cli" task-create --db-path "$db_path" --input "$task_payload" >/dev/null
python3 "$cli" snapshot-create --db-path "$db_path" --durability-dir "$durability_dir" --snapshot-id baseline --note "baseline snapshot" >/dev/null
python3 "$cli" task-update --db-path "$db_path" --task-id CENTRAL-OPS-9000 --expected-version 1 --input "$update_payload" >/dev/null
python3 "$cli" snapshot-create --db-path "$db_path" --durability-dir "$durability_dir" --snapshot-id updated --note "after task update" >/dev/null
python3 "$cli" snapshot-list --durability-dir "$durability_dir" --json >"$snapshot_list_json"
python3 "$cli" snapshot-restore --db-path "$restored_db" --durability-dir "$durability_dir" --snapshot-id updated >/dev/null
python3 "$cli" task-show --db-path "$restored_db" --task-id CENTRAL-OPS-9000 --json >"$restored_task_json"

python3 - "$durability_dir/latest.json" "$durability_dir/snapshots/updated/manifest.json" "$snapshot_list_json" "$restored_task_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    latest = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(sys.argv[3], encoding="utf-8") as handle:
    snapshot_list = json.load(handle)
with open(sys.argv[4], encoding="utf-8") as handle:
    restored_task = json.load(handle)

assert latest["snapshot_id"] == "updated", latest
assert manifest["task_count"] == 1, manifest
assert any(task["task_id"] == "CENTRAL-OPS-9000" and task["version"] == 2 for task in manifest["tasks"]), manifest
assert snapshot_list["count"] == 2, snapshot_list
assert snapshot_list["latest_snapshot_id"] == "updated", snapshot_list
assert restored_task["summary"] == "Updated summary after publish", restored_task
assert restored_task["planner_status"] == "in_progress", restored_task
assert restored_task["version"] == 2, restored_task
PY

echo "durability snapshot smoke passed"
