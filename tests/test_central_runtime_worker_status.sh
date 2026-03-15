#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_cli="$repo_root/scripts/central_runtime.py"
db_cli="$repo_root/scripts/central_task_db.py"
tmpdir="$(mktemp -d)"
runner_pid=""
trap 'if [[ -n "$runner_pid" ]] && kill -0 "$runner_pid" 2>/dev/null; then kill "$runner_pid" 2>/dev/null || true; fi; rm -rf "$tmpdir"' EXIT

db_path="$tmpdir/state/central_tasks.db"
state_dir="$tmpdir/runtime_state"
idle_json="$tmpdir/idle.json"
active_json="$tmpdir/active.json"
recent_json="$tmpdir/recent.json"
stuck_json="$tmpdir/stuck.json"
active_task_payload="$tmpdir/active-task.json"
stuck_task_payload="$tmpdir/stuck-task.json"

cat >"$active_task_payload" <<'JSON'
{
  "task_id": "CENTRAL-OPS-9100",
  "title": "Worker status active smoke",
  "summary": "Exercise active worker inspection",
  "objective_md": "Run a stub worker long enough to inspect live status.",
  "context_md": "Used only by the worker status smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "A live worker-status payload.",
  "acceptance_md": "worker-status reports a running task with heartbeat and log metadata.",
  "testing_md": "Run the worker status smoke test.",
  "dispatch_md": "Dispatch locally through the runtime smoke harness.",
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
    "test_case": "worker-status-active"
  },
  "execution": {
    "task_kind": "read_only",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "additional_writable_dirs": [],
    "timeout_seconds": 60,
    "metadata": {
      "stub_sleep_seconds": 4.0,
      "stub_log_interval_seconds": 0.4
    }
  },
  "dependencies": []
}
JSON

cat >"$stuck_task_payload" <<'JSON'
{
  "task_id": "CENTRAL-OPS-9101",
  "title": "Worker status stuck smoke",
  "summary": "Exercise stuck worker inspection",
  "objective_md": "Create a stale running lease for inspection.",
  "context_md": "Used only by the worker status smoke test.",
  "scope_md": "Temporary DB only.",
  "deliverables_md": "A stale worker-status payload.",
  "acceptance_md": "worker-status reports the run as potentially stuck.",
  "testing_md": "Run the worker status smoke test.",
  "dispatch_md": "Do not dispatch externally.",
  "closeout_md": "Smoke only.",
  "reconciliation_md": "Smoke only.",
  "planner_status": "todo",
  "priority": 2,
  "task_type": "implementation",
  "planner_owner": "planner/coordinator",
  "worker_owner": null,
  "target_repo_id": "CENTRAL",
  "target_repo_root": "/home/cobra/CENTRAL",
  "approval_required": false,
  "metadata": {
    "test_case": "worker-status-stuck"
  },
  "execution": {
    "task_kind": "read_only",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "additional_writable_dirs": [],
    "timeout_seconds": 60,
    "metadata": {}
  },
  "dependencies": []
}
JSON

python3 "$db_cli" init --db-path "$db_path" >/dev/null
python3 "$db_cli" repo-upsert --db-path "$db_path" --repo-id CENTRAL --repo-root /home/cobra/CENTRAL --display-name CENTRAL >/dev/null
python3 "$runtime_cli" worker-status --db-path "$db_path" --state-dir "$state_dir" --json >"$idle_json"

python3 "$db_cli" task-create --db-path "$db_path" --input "$active_task_payload" >/dev/null

python3 "$runtime_cli" run-once \
  --db-path "$db_path" \
  --state-dir "$state_dir" \
  --worker-mode stub \
  --poll-interval 0.2 \
  --heartbeat-seconds 0.5 \
  --status-heartbeat-seconds 0.5 \
  >"$tmpdir/run-once.log" 2>&1 &
runner_pid="$!"

for _ in $(seq 1 40); do
  python3 "$runtime_cli" worker-status \
    --db-path "$db_path" \
    --state-dir "$state_dir" \
    --task-id CENTRAL-OPS-9100 \
    --json >"$active_json"
  if python3 - "$active_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
raise SystemExit(0 if payload["summary"]["active_count"] >= 1 else 1)
PY
  then
    break
  fi
  sleep 0.2
done

wait "$runner_pid"
runner_pid=""

python3 "$runtime_cli" worker-status \
  --db-path "$db_path" \
  --state-dir "$state_dir" \
  --task-id CENTRAL-OPS-9100 \
  --json >"$recent_json"

python3 "$db_cli" task-create --db-path "$db_path" --input "$stuck_task_payload" >/dev/null
python3 "$db_cli" runtime-claim --db-path "$db_path" --worker-id stuck-worker --task-id CENTRAL-OPS-9101 >/dev/null
python3 "$db_cli" runtime-transition --db-path "$db_path" --task-id CENTRAL-OPS-9101 --status running --worker-id stuck-worker >/dev/null

python3 - "$db_path" "$state_dir" <<'PY'
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

db_path = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
now = datetime.now(timezone.utc).replace(microsecond=0)
claimed_at = (now - timedelta(minutes=12)).isoformat()
heartbeat_at = (now - timedelta(minutes=11)).isoformat()
expires_at = (now - timedelta(minutes=8)).isoformat()

conn = sqlite3.connect(str(db_path))
try:
    conn.execute(
        """
        UPDATE task_active_leases
        SET lease_acquired_at = ?,
            last_heartbeat_at = ?,
            lease_expires_at = ?,
            execution_run_id = ?
        WHERE task_id = ?
        """,
        (claimed_at, heartbeat_at, expires_at, "CENTRAL-OPS-9101-stale-run", "CENTRAL-OPS-9101"),
    )
    conn.execute(
        """
        UPDATE task_runtime_state
        SET claimed_at = ?,
            started_at = ?,
            last_transition_at = ?
        WHERE task_id = ?
        """,
        (claimed_at, claimed_at, heartbeat_at, "CENTRAL-OPS-9101"),
    )
    conn.commit()
finally:
    conn.close()

log_path = state_dir / ".worker-logs" / "CENTRAL-OPS-9101" / "CENTRAL-OPS-9101-stale-run.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
log_path.write_text("stale log line\n", encoding="utf-8")
old_timestamp = (now - timedelta(minutes=10)).timestamp()
os.utime(log_path, (old_timestamp, old_timestamp))
PY

python3 "$runtime_cli" worker-status \
  --db-path "$db_path" \
  --state-dir "$state_dir" \
  --task-id CENTRAL-OPS-9101 \
  --json >"$stuck_json"

python3 - "$idle_json" "$active_json" "$recent_json" "$stuck_json" <<'PY'
import json
from pathlib import Path
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    idle = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    active = json.load(handle)
with open(sys.argv[3], encoding="utf-8") as handle:
    recent = json.load(handle)
with open(sys.argv[4], encoding="utf-8") as handle:
    stuck = json.load(handle)

assert idle["summary"]["overall_status"] == "idle", idle
assert idle["summary"]["active_count"] == 0, idle
assert idle["runtime_paths"]["worker_results_dir"].endswith(".worker-results"), idle
assert Path(idle["runtime_paths"]["worker_results_dir"]).exists(), idle

assert active["summary"]["active_count"] == 1, active
active_worker = active["active_workers"][0]
assert active_worker["task_id"] == "CENTRAL-OPS-9100", active_worker
assert active_worker["runtime_status"] in {"claimed", "running"}, active_worker
assert active_worker["run_id"], active_worker
assert active_worker["heartbeat"]["age_seconds"] is not None, active_worker
assert active_worker["prompt"]["exists"] is True, active_worker
assert Path(active_worker["result"]["path"]).suffix == ".json", active_worker
assert ".worker-results" in Path(active_worker["result"]["path"]).parts, active_worker
assert active_worker["observed_state"] in {"healthy", "low_activity"}, active_worker

recent_worker = next(worker for worker in recent["recent_workers"] if worker["task_id"] == "CENTRAL-OPS-9100")
assert recent["summary"]["active_count"] == 0, recent
assert recent_worker["runtime_status"] == "done", recent_worker
assert recent_worker["run_id"], recent_worker
assert recent_worker["result"]["exists"] is True, recent_worker
result_path = Path(recent_worker["result"]["path"])
assert result_path.suffix == ".json", recent_worker
assert ".worker-results" in result_path.parts, recent_worker
assert "CENTRAL-OPS-9100" in result_path.parts, recent_worker

assert stuck["summary"]["overall_status"] == "potentially_stuck", stuck
assert stuck["summary"]["active_count"] == 1, stuck
stuck_worker = stuck["active_workers"][0]
assert stuck_worker["task_id"] == "CENTRAL-OPS-9101", stuck_worker
assert stuck_worker["observed_state"] == "potentially_stuck", stuck_worker
assert stuck_worker["heartbeat"]["seconds_until_lease_expiry"] is not None and stuck_worker["heartbeat"]["seconds_until_lease_expiry"] < 0, stuck_worker
PY

test ! -e "$state_dir/.worker-reports"

echo "central runtime worker-status smoke passed"
