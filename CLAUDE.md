# CENTRAL

When working in this repo, assume the multi-repo-planner role described in [skills/multi-repo-planner/SKILL.md](skills/multi-repo-planner/SKILL.md). Read that file for the full operating model, dispatch contract, and worker contract.

When the user says "on mobile", "mobile response", "mobile mode", or "from my phone", read and follow [skills/mobile-response/SKILL.md](skills/mobile-response/SKILL.md) for compressed small-screen output.

Key references:
- DB CLI: `python3 scripts/central_task_db.py --help`
- Runtime: `python3 scripts/central_runtime.py --help`
- Dispatcher: `python3 scripts/dispatcher_control.py --help`
- Dispatch snippets: `skills/multi-repo-planner/references/dispatch-and-status.md`

## Critical: machine-specific paths

This repo was originally developed on a machine at `/home/cobra/`. It now runs on `/Users/paul/projects/`.

**`/home/cobra/photo_auto_tagging` does not exist on this machine.** The Dispatcher/autonomy module lives at `/Users/paul/projects/Dispatcher`.

- `AUTONOMY_ROOT` in `scripts/central_runtime.py` **must** use the env-overridable form: `Path(os.environ.get("CENTRAL_AUTONOMY_ROOT", str(REPO_ROOT.parent / "Dispatcher")))`. Do not change it to a hardcoded path.
- Any doc references to `/home/cobra/...` are stale. The canonical paths are `/Users/paul/projects/CENTRAL` and `/Users/paul/projects/Dispatcher`.
