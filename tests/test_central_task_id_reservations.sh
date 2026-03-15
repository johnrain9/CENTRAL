#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$repo_root/scripts/central_task_db.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
next_initial_json="$tmpdir/next-initial.json"
next_after_tasks_json="$tmpdir/next-after-tasks.json"
next_after_reserve_json="$tmpdir/next-after-reserve.json"
next_after_expire_json="$tmpdir/next-after-expire.json"
reserve_one_json="$tmpdir/reserve-one.json"
reserve_two_json="$tmpdir/reserve-two.json"
reservations_after_fill_json="$tmpdir/reservations-after-fill.json"
reservations_final_json="$tmpdir/reservations-final.json"

write_task_payload() {
  local task_id="$1"
  local output_path="$2"
  cat >"$output_path" <<JSON
{
  "task_id": "$task_id",
  "title": "Reservation smoke for $task_id",
  "summary": "Exercise task ID reservation behavior",
  "objective_md": "Create a task record for reservation smoke coverage.",
  "context_md": "Used only by the task ID reservation smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "A task row in the smoke-test DB.",
  "acceptance_md": "Task creation succeeds and can reconcile reservations.",
  "testing_md": "Run the task ID reservation smoke test.",
  "dispatch_md": "Do not dispatch.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": 1,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "/home/cobra/CENTRAL",
  "approval_required": false,
  "metadata": {
    "test_case": "task-id-reservation"
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
}

create_task() {
  local task_id="$1"
  local payload_path="$tmpdir/$task_id.json"
  write_task_payload "$task_id" "$payload_path"
  python3 "$cli" task-create --db-path "$db_path" --input "$payload_path" >/dev/null
}

python3 "$cli" init --db-path "$db_path" >/dev/null
python3 "$cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root /home/cobra/CENTRAL --display-name CENTRAL >/dev/null

python3 "$cli" task-id-next --db-path "$db_path" --series CENTRAL-OPS --json >"$next_initial_json"

create_task CENTRAL-OPS-1
create_task CENTRAL-OPS-2

python3 "$cli" task-id-next --db-path "$db_path" --series CENTRAL-OPS --json >"$next_after_tasks_json"
python3 "$cli" task-id-reserve \
  --db-path "$db_path" \
  --series CENTRAL-OPS \
  --count 3 \
  --hours 24 \
  --reserved-for "reservation smoke family" \
  --note "first contiguous range" \
  --json >"$reserve_one_json"
python3 "$cli" task-id-next --db-path "$db_path" --series CENTRAL-OPS --json >"$next_after_reserve_json"
python3 "$cli" task-id-reserve \
  --db-path "$db_path" \
  --series CENTRAL-OPS \
  --count 2 \
  --hours 24 \
  --reserved-for "follow-on family" \
  --note "second contiguous range" \
  --json >"$reserve_two_json"

create_task CENTRAL-OPS-3
create_task CENTRAL-OPS-4
create_task CENTRAL-OPS-5

python3 "$cli" task-id-reservations --db-path "$db_path" --series CENTRAL-OPS --all --include-events --json >"$reservations_after_fill_json"

python3 - "$db_path" <<'PY'
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

db_path = sys.argv[1]
expired_at = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()

conn = sqlite3.connect(db_path)
try:
    conn.execute(
        """
        UPDATE task_id_reservations
        SET expires_at = ?, updated_at = ?
        WHERE reserved_for = ?
        """,
        (expired_at, expired_at, "follow-on family"),
    )
    conn.commit()
finally:
    conn.close()
PY

python3 "$cli" task-id-next --db-path "$db_path" --series CENTRAL-OPS --json >"$next_after_expire_json"
python3 "$cli" task-id-reservations --db-path "$db_path" --series CENTRAL-OPS --all --include-events --json >"$reservations_final_json"

python3 - \
  "$next_initial_json" \
  "$next_after_tasks_json" \
  "$reserve_one_json" \
  "$next_after_reserve_json" \
  "$reserve_two_json" \
  "$reservations_after_fill_json" \
  "$next_after_expire_json" \
  "$reservations_final_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    next_initial = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    next_after_tasks = json.load(handle)
with open(sys.argv[3], encoding="utf-8") as handle:
    reserve_one = json.load(handle)
with open(sys.argv[4], encoding="utf-8") as handle:
    next_after_reserve = json.load(handle)
with open(sys.argv[5], encoding="utf-8") as handle:
    reserve_two = json.load(handle)
with open(sys.argv[6], encoding="utf-8") as handle:
    reservations_after_fill = json.load(handle)
with open(sys.argv[7], encoding="utf-8") as handle:
    next_after_expire = json.load(handle)
with open(sys.argv[8], encoding="utf-8") as handle:
    reservations_final = json.load(handle)

assert next_initial["next_task_id"] == "CENTRAL-OPS-1", next_initial
assert next_initial["highest_existing_number"] == 0, next_initial
assert next_after_tasks["next_task_id"] == "CENTRAL-OPS-3", next_after_tasks
assert reserve_one["task_ids"] == ["CENTRAL-OPS-3", "CENTRAL-OPS-4", "CENTRAL-OPS-5"], reserve_one
assert reserve_one["status"] == "active", reserve_one
assert reserve_one["events"][0]["event_type"] == "planner.task_id_reservation_created", reserve_one
assert next_after_reserve["next_task_id"] == "CENTRAL-OPS-6", next_after_reserve
assert reserve_two["task_ids"] == ["CENTRAL-OPS-6", "CENTRAL-OPS-7"], reserve_two
assert reserve_two["reservation_id"] != reserve_one["reservation_id"], (reserve_one, reserve_two)

after_fill = {item["reserved_for"]: item for item in reservations_after_fill}
first = after_fill["reservation smoke family"]
second = after_fill["follow-on family"]
assert first["status"] == "completed", first
assert first["open_count"] == 0, first
assert first["existing_task_ids"] == ["CENTRAL-OPS-3", "CENTRAL-OPS-4", "CENTRAL-OPS-5"], first
assert any(event["event_type"] == "planner.task_id_reservation_completed" for event in first["events"]), first
assert second["status"] == "active", second
assert second["open_task_ids"] == ["CENTRAL-OPS-6", "CENTRAL-OPS-7"], second

assert next_after_expire["next_task_id"] == "CENTRAL-OPS-6", next_after_expire

final_rows = {item["reserved_for"]: item for item in reservations_final}
assert final_rows["reservation smoke family"]["status"] == "completed", final_rows
expired = final_rows["follow-on family"]
assert expired["status"] == "expired", expired
assert any(event["event_type"] == "planner.task_id_reservation_expired" for event in expired["events"]), expired
PY

echo "task ID reservation smoke passed"
