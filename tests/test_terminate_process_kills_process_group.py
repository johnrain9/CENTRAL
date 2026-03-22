#!/usr/bin/env python3
"""Regression test: terminate_process kills child processes via process group.

Covers the bug where os.kill(pid, SIGTERM) only killed the Python wrapper,
leaving cargo builds / claude subprocesses as orphans that held file locks.

Fix: subprocess.Popen(..., start_new_session=True) + os.killpg(pgid, SIGTERM).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from central_runtime_v2.dispatcher import terminate_process  # noqa: E402

# Python snippet that spawns a child sleep and prints its PID, then sleeps.
# The child is spawned WITHOUT start_new_session, so it inherits the parent's
# process group — exactly like cargo/claude subprocesses spawned by a worker.
_PARENT_SCRIPT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen(['sleep', '9999'])\n"
    "sys.stdout.write(str(child.pid) + '\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(9999)\n"
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TerminateProcessKillsGroupTest(unittest.TestCase):
    def test_terminate_kills_child_processes(self) -> None:
        """terminate_process must reap children, not just the wrapper PID.

        Regression for the bug where os.kill(pid, SIGTERM) left cargo/claude
        subprocesses alive as orphans, causing file-lock contention for the
        next worker.
        """
        proc = subprocess.Popen(
            [sys.executable, "-c", _PARENT_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        child_pid = int(proc.stdout.readline().strip())  # type: ignore[union-attr]
        pgid = os.getpgid(proc.pid)

        try:
            self.assertTrue(_pid_alive(proc.pid), "parent should be alive before terminate")
            self.assertTrue(_pid_alive(child_pid), "child should be alive before terminate")

            terminate_process(proc.pid, proc, pgid=pgid)

            # SIGTERM propagates immediately via os.killpg; allow up to 2 s.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if not _pid_alive(child_pid):
                    break
                time.sleep(0.05)

            self.assertFalse(
                _pid_alive(child_pid),
                f"child PID {child_pid} should be dead after terminate_process "
                f"(orphan-process-group bug reproduced if this fails)",
            )
        finally:
            # Best-effort cleanup so stray sleeps don't accumulate.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    def test_start_new_session_creates_new_pgid(self) -> None:
        """Workers spawned with start_new_session=True must get their own pgid."""
        proc = subprocess.Popen(
            ["sleep", "9999"],
            start_new_session=True,
        )
        try:
            pgid = os.getpgid(proc.pid)
            # With start_new_session=True the child is its own group leader.
            self.assertEqual(pgid, proc.pid)
            # The child's pgid must differ from the test process's own pgid.
            self.assertNotEqual(pgid, os.getpgid(os.getpid()))
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()


if __name__ == "__main__":
    unittest.main()
