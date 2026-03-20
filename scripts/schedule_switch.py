#!/usr/bin/env python3
"""Schedule dispatcher mode switches.

Usage:
  python3 scripts/schedule_switch.py schedule --at "2am" --mode codex
  python3 scripts/schedule_switch.py schedule --at "14:30" --mode claude --model claude-sonnet-4-6 --workers 2
  python3 scripts/schedule_switch.py list
  python3 scripts/schedule_switch.py cancel <id>
  python3 scripts/schedule_switch.py cancel --all
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DISPATCHER_SCRIPT = SCRIPT_DIR / "dispatcher_control.py"
STATE_FILE = SCRIPT_DIR.parent / "state" / "scheduled_switches.json"

DEFAULT_MODELS = {
    "codex": "gpt-5.4",
    "claude": "claude-sonnet-4-6",
}
DEFAULT_WORKERS = 4


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def parse_time(time_str: str) -> datetime.datetime:
    """Parse human time strings: '2am', '2:30am', '14:30', '2:00 AM'."""
    now = datetime.datetime.now()
    s = time_str.strip().upper().replace(" ", "")
    for fmt in ["%I%p", "%I:%M%p", "%H:%M", "%H:%M:%S"]:
        try:
            t = datetime.datetime.strptime(s, fmt)
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return target
        except ValueError:
            continue
    raise ValueError(f"Cannot parse time: {time_str!r}  (try '2am', '14:30', '2:30am')")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_switches() -> list[dict]:
    if not STATE_FILE.exists():
        return []
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_switches(switches: list[dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(switches, indent=2, default=str))


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def switch_status(sw: dict) -> str:
    target = datetime.datetime.fromisoformat(sw["scheduled_at"])
    pid = sw.get("pid")
    if pid and pid_alive(pid):
        return "pending"
    if target > datetime.datetime.now():
        return "cancelled"
    return "fired"


def eta_str(target: datetime.datetime) -> str:
    delta = target - datetime.datetime.now()
    h = int(delta.total_seconds() // 3600)
    m = int((delta.total_seconds() % 3600) // 60)
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_schedule(args: argparse.Namespace) -> None:
    target = parse_time(args.at)
    delay = int((target - datetime.datetime.now()).total_seconds())
    mode = args.mode
    model = args.model or DEFAULT_MODELS[mode]
    workers = args.workers or DEFAULT_WORKERS

    switch_id = f"sw-{int(datetime.datetime.now().timestamp())}"
    log_path = f"/tmp/dispatcher-switch-{switch_id}.log"

    restart_cmd = (
        f"python3 {DISPATCHER_SCRIPT} restart"
        f" --max-workers {workers}"
        f" --worker-mode {mode}"
        f" --worker-model {model}"
    )

    script = "\n".join([
        f'echo "[$(date)] [{switch_id}] sleeping {delay}s, fires at {target.strftime("%I:%M %p")}"',
        f"sleep {delay}",
        f'echo "[$(date)] [{switch_id}] executing: {restart_cmd}"',
        restart_cmd,
        f'echo "[$(date)] [{switch_id}] done."',
    ])

    proc = subprocess.Popen(
        ["bash", "-c", script],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,  # detach from terminal session
    )

    sw = {
        "id": switch_id,
        "pid": proc.pid,
        "scheduled_at": target.isoformat(),
        "created_at": datetime.datetime.now().isoformat(),
        "mode": mode,
        "model": model,
        "max_workers": workers,
        "log_path": log_path,
    }
    switches = load_switches()
    switches.append(sw)
    save_switches(switches)

    print(f"Scheduled [{switch_id}]")
    print(f"  fires at : {target.strftime('%I:%M %p')} ({eta_str(target)} from now)")
    print(f"  mode     : {mode} / {model} x{workers} workers")
    print(f"  log      : {log_path}")
    if args.json:
        print(json.dumps(sw, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    switches = load_switches()
    if not switches:
        print("No scheduled switches.")
        return

    rows = []
    for sw in switches:
        status = switch_status(sw)
        target = datetime.datetime.fromisoformat(sw["scheduled_at"])
        rows.append({
            "id": sw["id"],
            "status": status,
            "at": target.strftime("%I:%M %p"),
            "mode": sw["mode"],
            "model": sw["model"],
            "workers": sw["max_workers"],
            "eta": eta_str(target) if status == "pending" else "-",
        })

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    print(f"{'ID':<24} {'STATUS':<10} {'AT':<10} {'MODE':<8} {'MODEL':<24} {'WRK':<5} ETA")
    print("-" * 90)
    for r in rows:
        print(f"{r['id']:<24} {r['status']:<10} {r['at']:<10} {r['mode']:<8} {r['model']:<24} {r['workers']:<5} {r['eta']}")


def cmd_cancel(args: argparse.Namespace) -> None:
    switches = load_switches()

    if args.all:
        cancelled = []
        for sw in switches:
            if switch_status(sw) == "pending":
                pid = sw.get("pid")
                if pid:
                    try:
                        os.kill(pid, 15)
                        cancelled.append(sw["id"])
                    except (ProcessLookupError, PermissionError):
                        cancelled.append(sw["id"])
        switches = [sw for sw in switches if switch_status(sw) != "pending"]
        save_switches(switches)
        print(f"Cancelled {len(cancelled)} switch(es)" + (f": {', '.join(cancelled)}" if cancelled else "."))
        return

    if not args.id:
        print("Error: specify a switch ID or --all", file=sys.stderr)
        sys.exit(1)

    match = next((sw for sw in switches if sw["id"] == args.id), None)
    if not match:
        print(f"Switch {args.id!r} not found.", file=sys.stderr)
        sys.exit(1)

    status = switch_status(match)
    if status != "pending":
        print(f"Switch {args.id} is already {status}, nothing to cancel.")
        return

    pid = match.get("pid")
    if pid:
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError) as exc:
            print(f"Warning: could not signal pid {pid}: {exc}")

    switches = [sw for sw in switches if sw["id"] != args.id]
    save_switches(switches)
    print(f"Cancelled [{args.id}]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schedule dispatcher mode switches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("schedule", help="Schedule a dispatcher switch")
    p.add_argument("--at", required=True, help="Time to fire (e.g. '2am', '14:30', '2:30am')")
    p.add_argument("--mode", required=True, choices=["claude", "codex"], help="Worker mode")
    p.add_argument("--model", help=f"Model override (defaults: {DEFAULT_MODELS})")
    p.add_argument("--workers", type=int, help=f"Max workers (default: {DEFAULT_WORKERS})")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="List all scheduled switches")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("cancel", help="Cancel a pending switch")
    p.add_argument("id", nargs="?", help="Switch ID to cancel")
    p.add_argument("--all", action="store_true", help="Cancel all pending switches")

    args = parser.parse_args()
    if args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "cancel":
        cmd_cancel(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
