#!/usr/bin/env bash
# <swiftbar.title>CENTRAL Pulse</swiftbar.title>
# <swiftbar.version>1.0</swiftbar.version>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

DB="/Users/paul/projects/CENTRAL/state/central_tasks.db"
PLANNER_URL="http://localhost:7099/"

/opt/homebrew/bin/python3 - "$DB" "$PLANNER_URL" <<'PYEOF'
import sys, sqlite3

db_path = sys.argv[1]
planner_url = sys.argv[2]

try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
except Exception as e:
    print("CENTRAL ⚠️")
    print("---")
    print(f"DB error: {e}")
    sys.exit(0)

try:
    running = conn.execute(
        "SELECT COUNT(*) FROM task_runtime_state WHERE runtime_status='running'"
    ).fetchone()[0]

    queued = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE planner_status='todo'"
    ).fetchone()[0]

    auditing = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE planner_status='awaiting_audit'"
    ).fetchone()[0]

    # Failed + not already closed by planner
    attention = conn.execute(
        """SELECT COUNT(*) FROM task_runtime_state trs
           JOIN tasks t ON t.task_id = trs.task_id
           WHERE trs.runtime_status = 'failed'
           AND t.planner_status NOT IN ('done', 'cancelled')"""
    ).fetchone()[0]

    failed_tasks = conn.execute(
        """SELECT t.task_id, t.title, trs.last_runtime_error
           FROM task_runtime_state trs
           JOIN tasks t ON t.task_id = trs.task_id
           WHERE trs.runtime_status = 'failed'
           AND t.planner_status NOT IN ('done', 'cancelled')
           ORDER BY trs.last_transition_at DESC
           LIMIT 8"""
    ).fetchall()

    running_tasks = conn.execute(
        """SELECT t.task_id, t.title
           FROM task_runtime_state trs
           JOIN tasks t ON t.task_id = trs.task_id
           WHERE trs.runtime_status = 'running'
           ORDER BY trs.last_transition_at ASC
           LIMIT 8"""
    ).fetchall()

    conn.close()
except Exception as e:
    print("CENTRAL ⚠️")
    print("---")
    print(f"Query error: {e}")
    sys.exit(0)

# ── menu bar line ──────────────────────────────────────────
parts = []
if attention:
    parts.append(f"🔴{attention}")
if running:
    parts.append(f"⚙️{running}")
if queued:
    parts.append(f"📋{queued}")
if auditing:
    parts.append(f"🔎{auditing}")

bar_line = "CENTRAL " + (" ".join(parts) if parts else "✓")
print(bar_line)
print("---")

# ── dropdown ───────────────────────────────────────────────
if attention and failed_tasks:
    print("NEEDS ATTENTION | color=red font=bold")
    for task_id, title, error in failed_tasks:
        label = f"  🔴 {task_id}  {(title or '')[:45]}"
        url = f"http://localhost:7099/#{task_id}"
        print(f"{label} | href={url} color=red")
    print("---")

if running_tasks:
    print(f"RUNNING ({running})")
    for task_id, title in running_tasks:
        label = f"  ⚙️ {task_id}  {(title or '')[:45]}"
        url = f"http://localhost:7099/#{task_id}"
        print(f"{label} | href={url}")
    print("---")

summary_parts = []
if queued:
    summary_parts.append(f"📋 {queued} queued")
if auditing:
    summary_parts.append(f"🔎 {auditing} in audit")
if summary_parts:
    print("  ".join(summary_parts) + " | color=#888888")
    print("---")

print(f"Open Planner | href={planner_url}")
print("Refresh | refresh=true")
PYEOF
