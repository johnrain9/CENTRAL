"""Logging utilities for the CENTRAL runtime daemon."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path


class DaemonLog:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def emit(self, level: str, subsystem: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {level} [{subsystem}] {message}"
        print(self._format_console_line(timestamp, level, subsystem, message))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # Pattern matches the plain-text log format written by emit():
    #   HH:MM:SS LEV [subsystem] message
    _LOG_LINE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}) (INF|WRN|ERR|DBG) \[([^\]]+)\] (.*)$")

    def colorize_log_line(self, line: str) -> str:
        """Re-parse a plain-text log line and apply color formatting for TTY output."""
        m = self._LOG_LINE_RE.match(line)
        if not m:
            return line
        timestamp, level, subsystem, message = m.group(1), m.group(2), m.group(3), m.group(4)
        return self._format_console_line(timestamp, level, subsystem, message)

    def tail(self, lines: int = 120, colorize: bool = False) -> str:
        if not self.path.exists():
            return ""
        data = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail_lines = data[-lines:]
        if colorize:
            tail_lines = [self.colorize_log_line(ln) for ln in tail_lines]
        return "\n".join(tail_lines)

    def _style(self, text: str, *codes: str) -> str:
        if not self.use_color:
            return text
        return "".join(codes) + text + self.RESET

    def _truncate(self, s: str | None, n: int) -> str:
        """Truncate s to at most n chars with ellipsis; handles None safely."""
        if not s:
            return ""
        return s if len(s) <= n else s[: n - 1] + "…"

    def _kv(self, message: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, val in re.findall(r'([A-Za-z_]+)="([^"]*)"', message):
            result[key] = val
        for key, val in re.findall(r'([A-Za-z_]+)=([^ "]+)', message):
            if key not in result:
                result[key] = val
        return result

    def _prefix(self, timestamp: str, level: str, subsystem: str) -> str:
        level_color = {
            "INF": self.CYAN,
            "WRN": self.YELLOW,
            "ERR": self.RED,
        }.get(level, self.BLUE)
        ts = self._style(timestamp, self.DIM)
        lvl = self._style(level, self.BOLD, level_color)
        sub = self._style(f"[{subsystem}]", self.MAGENTA)
        return f"{ts} {lvl} {sub}"

    def _format_console_line(self, timestamp: str, level: str, subsystem: str, message: str) -> str:
        prefix = self._prefix(timestamp, level, subsystem)
        if subsystem != "central.dispatcher":
            return f"{prefix} {message}"

        fields = self._kv(message)
        ts = self._style(timestamp, self.DIM)
        task_id = fields.get("task", "")
        title = fields.get("title", "")

        def task_line(icon: str, verb: str, verb_color: str, extra: str = "", extra_color: str = "") -> str:
            # Fixed-width columns: verb=8 chars, task_id=20 chars (padding kept outside ANSI codes)
            verb_s = self._style(verb, self.BOLD, verb_color) + " " * max(1, 8 - len(verb))
            task_s = self._style(task_id, self.BOLD) + " " * max(2, 20 - len(task_id))
            line = f"{ts} {icon} {verb_s} {task_s}"
            if title:
                line += self._style(self._truncate(title, 45), self.DIM)
            if extra:
                line += "  " + self._style(extra, self.BOLD, extra_color)
            return line

        if message.startswith("heartbeat "):
            failed = fields.get('failed', '0')
            mismatch = fields.get('mismatch', '0')
            review = fields.get('review', '0')
            next_task = fields.get('next', '-')
            return (
                f"{self._style(timestamp, self.DIM)} "
                f"{self._style('♥', self.BOLD, self.BLUE)} "
                f"{self._style(fields.get('workers', '-'), self.BOLD, self.CYAN)} "
                f"tasks={self._style(fields.get('running_tasks', '-'), self.GREEN)} "
                f"eligible={self._style(fields.get('eligible', '-'), self.CYAN)}"
                + (f" next={self._style(next_task, self.GREEN)}" if next_task != '-' else "")
                + (f" review={self._style(review, self.YELLOW)}" if review != '0' else "")
                + (f" failed={self._style(failed, self.RED)}" if failed != '0' else "")
                + (f" mismatch={self._style(mismatch, self.RED)}" if mismatch != '0' else "")
            )
        if message.startswith("worker_spawned "):
            mode = fields.get("mode", "-")
            model = fields.get("model", "")
            extra = f"mode={mode}"
            if model and model not in ("-", ""):
                extra += f" model={model}"
            return task_line("→", "START", self.GREEN, extra, self.CYAN)
        if message.startswith("worker_finished "):
            status = fields.get("runtime_status", "-")
            status_color = self.GREEN if status in {"done", "pending_review"} else self.RED
            icon = "✓" if status in {"done", "pending_review"} else "✗"
            return task_line(icon, "FINISH", status_color, status, status_color)
        if message.startswith("worker_timeout "):
            return task_line("⏱", "TIMEOUT", self.YELLOW, "timeout", self.YELLOW)
        if message.startswith("worker_audit_rework "):
            parent = fields.get("parent", "-")
            return task_line("↺", "REWORK", self.RED, f"parent={parent}", self.RED)
        if message.startswith("worker_audit_pass "):
            parent = fields.get("parent", "-")
            return task_line("✓", "PASS", self.GREEN, f"parent={parent}", self.GREEN)
        if message.startswith("worker_auto_reconcile_skipped "):
            return task_line("·", "QUEUED", self.YELLOW, "awaiting_audit", self.YELLOW)
        if message.startswith("worker_auto_reconciled "):
            status = fields.get("planner_status", "-")
            return task_line("✓", "RECONC", self.GREEN, status, self.GREEN)
        if message.startswith("worker_heartbeat "):
            return task_line("·", "HBEAT", self.CYAN)
        if message.startswith("worker_capacity_requeued "):
            return task_line("·", "REQUEUE", self.YELLOW, "capacity", self.YELLOW)
        if message.startswith("stale_recovery "):
            return (
                f"{prefix} "
                f"{self._style('RECOVER', self.BOLD, self.YELLOW)} "
                f"recovered={self._style(fields.get('recovered', '-'), self.BOLD, self.YELLOW)}"
            )
        if message.startswith("spark_quota_exhausted "):
            fallback = fields.get("fallback_model", "-")
            return (
                f"{prefix} {self._style('SPARK EXHAUSTED', self.BOLD, self.RED)} "
                f"{self._style('→ switching to', self.DIM)} "
                f"{self._style(fallback, self.BOLD, self.YELLOW)} "
                f"{self._style('(permanent)', self.DIM)}"
            )
        if message.startswith("dispatcher_started"):
            return f"{prefix} {self._style('DISPATCHER STARTED', self.BOLD, self.GREEN)} {message}"
        if message.startswith("dispatcher_stopped"):
            return f"{prefix} {self._style('DISPATCHER STOPPED', self.BOLD, self.YELLOW)}"
        if message.startswith("worker_spawn_error") or message.startswith("worker_heartbeat_error"):
            if task_id:
                return task_line("✗", "ISSUE", self.RED, str(fields.get("error", ""))[:60], self.RED)
            return f"{prefix} {self._style('ISSUE', self.BOLD, self.RED)} {message}"
        if message.startswith("dispatcher_handoff_prepared") or message.startswith("worker_adopted"):
            return f"{prefix} {self._style('HANDOFF', self.BOLD, self.YELLOW)} {message}"
        if message.startswith("worker_auto_reconcile_failed"):
            if task_id:
                return task_line("✗", "RECONC", self.RED, str(fields.get("error", ""))[:60], self.RED)
            return f"{prefix} {self._style('RECONCILE', self.BOLD, self.RED)} {message}"
        return f"{prefix} {message}"
