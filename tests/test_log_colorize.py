#!/usr/bin/env python3
"""Regression test: DaemonLog.colorize_log_line() and tail(colorize=True).

Root cause (CENTRAL-OPS-84): command_tail used os.execvp("tail", ...) which
streams the plain-text log file verbatim — no ANSI color even on a TTY.
Fix: Python follow loop + colorize_log_line() applied per-line when is_tty.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import central_runtime


def _make_log(tmp: Path) -> central_runtime.DaemonLog:
    """Build a DaemonLog whose use_color is forced True for testing."""
    paths = SimpleNamespace(log_path=tmp / "dispatcher.log")
    log = central_runtime.DaemonLog.__new__(central_runtime.DaemonLog)
    log.path = paths.log_path
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.use_color = True  # force color regardless of TTY
    return log


PLAIN_LINES = [
    "12:34:56 INF [central.dispatcher] heartbeat state=running workers=2/3 idle=1 active=FOO-1,BAR-2 | ready_now=4 next_ready=BAZ-3 leases=2 | parked_total=2 parked_reasons=dependency-blocked:2 parked_sample=WAIT-1,WAIT-2 | review_queue=1 failed_queue=1 mismatch=0",
    "12:34:57 INF [central.dispatcher] worker_spawned task=FOO-1 run=r1 pid=1234 mode=claude model=gpt-5.4",
    "12:34:58 ERR [central.dispatcher] worker_spawn_error task=BAR-2 reason=timeout",
    "12:34:59 WRN [central.worker] some plain message",
    "not a valid log line",
]


class TestColorizeLogLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = _make_log(Path(self.tmp))

    def test_valid_lines_contain_ansi(self):
        for line in PLAIN_LINES[:-1]:  # skip invalid line
            result = self.log.colorize_log_line(line)
            self.assertIn("\033[", result, f"Expected ANSI codes in: {result!r}")

    def test_invalid_line_returned_verbatim(self):
        result = self.log.colorize_log_line("not a valid log line")
        self.assertEqual(result, "not a valid log line")

    def test_colorize_log_line_regex_pattern(self):
        # Confirm the regex matches the exact format written by DaemonLog.emit()
        m = central_runtime.DaemonLog._LOG_LINE_RE.match(PLAIN_LINES[0])
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "12:34:56")
        self.assertEqual(m.group(2), "INF")
        self.assertEqual(m.group(3), "central.dispatcher")

    def test_tail_colorize_true(self):
        log_path = self.log.path
        log_path.write_text("\n".join(PLAIN_LINES) + "\n", encoding="utf-8")
        result = self.log.tail(lines=10, colorize=True)
        # Should have ANSI codes for the valid lines
        self.assertIn("\033[", result)

    def test_heartbeat_uses_queue_bucket_labels(self):
        result = self.log.colorize_log_line(PLAIN_LINES[0])
        self.assertIn("HEARTBEAT", result)
        self.assertIn("review=", result)
        self.assertIn("failed=", result)
        self.assertIn("mismatch=", result)

    def test_tail_colorize_false(self):
        log_path = self.log.path
        log_path.write_text("\n".join(PLAIN_LINES) + "\n", encoding="utf-8")
        result = self.log.tail(lines=10, colorize=False)
        # Plain text — no ANSI codes
        self.assertNotIn("\033[", result)

    def test_tail_empty_file(self):
        self.log.path.write_text("", encoding="utf-8")
        self.assertEqual(self.log.tail(colorize=True), "")

    def test_tail_missing_file(self):
        # path does not exist
        self.assertEqual(self.log.tail(colorize=True), "")


if __name__ == "__main__":
    unittest.main()
