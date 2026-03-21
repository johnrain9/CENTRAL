#!/usr/bin/env python3
"""PATH-friendly interactive menu entrypoint for dispatcher operations."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dispatcher_control


def main(argv: list[str]) -> int:
    return dispatcher_control.main([argv[0], "menu", *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
