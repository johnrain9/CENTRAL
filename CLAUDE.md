# CENTRAL

When working in this repo, assume the multi-repo-planner role described in [skills/multi-repo-planner/SKILL.md](skills/multi-repo-planner/SKILL.md). Read that file for the full operating model, dispatch contract, and worker contract.

When the user says "on mobile", "mobile response", "mobile mode", or "from my phone", read and follow [skills/mobile-response/SKILL.md](skills/mobile-response/SKILL.md) for compressed small-screen output.

## Task management skills (use these — never run the ceremony manually)

- `/task-create` — create any task; handles scaffolding + preflight automatically via `task_quick.py`
- `/task-view TASK-ID` — show full detail for a specific task
- `/task-list` — portfolio summary, eligible work, active tasks

**Rule:** Any time a task needs to be created, invoke `/task-create`. Do not run `task-preflight`, `planner-new`, or `task-create` DB commands directly.

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
