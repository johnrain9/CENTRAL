import sys
from pathlib import Path

# Ensure the scripts/ directory is on sys.path so this file can be run directly
# (e.g. `python scripts/central_runtime_v2/__main__.py`) without needing a package install.
_scripts_dir = str(Path(__file__).resolve().parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from central_runtime_v2.commands import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
