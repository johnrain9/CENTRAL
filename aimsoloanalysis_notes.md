# aimSoloAnalysis Notes

Last reviewed: 2026-03-09
Repo: `/home/cobra/aimSoloAnalysis`

## Current State
- Branch: `feature/task-p0-12-evidence-plumbing`
- Last commit at review time: `54804fb` (`2026-03-08`, `chore: commit all current workspace changes`)
- Repo was dirty at review time:
  - `TASKS.md` modified
  - `docs/wsl2_native_js_ui_design.md` untracked

## What The Repo Does
- Local/offline-first Aim Solo motorsports analysis app.
- Ingests telemetry CSVs into standardized `RunData`, persists to SQLite, computes trackside analytics, and serves a local API/UI.
- Current frontend is a small static JS app under `ui/`; there is active design intake for a native WSL2 workflow and a rewritten modern JS frontend.

## Current Task Board
- Source of truth: `/home/cobra/aimSoloAnalysis/TASKS.md`
- Task counts at review time:
  - `done=57`
  - `in_progress=1`
  - `todo=21`
- Existing `in-progress` thread:
  - `XRK R&D notes (PROGRESS_AIMSOLO_XRK.txt)`

## Most Relevant Open Work
- P0 coaching contract and evidence chain:
  - `TASK-P0-10`
  - `TASK-P0-11`
  - `TASK-P0-12`
  - `TASK-P0-13`
  - `TASK-P0-14`
- Platform/UI intake now encoded in task board:
  - `TASK-PLAT-01`
  - `TASK-PLAT-02`
  - `TASK-UI-10`
  - `TASK-UI-11`
  - `TASK-UI-12`
  - `TASK-UI-13`
  - `TASK-UI-14`

## Key Planning Insight
- The WSL2-native runtime and rewritten UI initiative is valid, but it should not outrun the P0 coaching contract chain.
- `TASK-P0-10` is the first hard gate for both:
  - did-vs-should coaching quality
  - rewritten UI contract freeze (`TASK-UI-10`)
- `TASK-UI-12` should not start until both of these are complete:
  - `TASK-PLAT-02`
  - `TASK-UI-11`

## Dependency Chain To Respect
Planner order derived from `TASKS.md`:

1. `TASK-P0-10`
2. `TASK-P0-11` and `TASK-P0-12`
3. `TASK-P0-13`
4. `TASK-P0-14`
5. `TASK-PLAT-01`
6. `TASK-PLAT-02`
7. `TASK-UI-10`
8. `TASK-UI-11`
9. `TASK-UI-12`
10. `TASK-UI-13`
11. `TASK-UI-14`

## Recommended Next 5 Dispatches
Dispatch one task at a time by default.

1. `TASK-P0-10`
2. `TASK-P0-12`
3. `TASK-P0-11`
4. `TASK-P0-13`
5. `TASK-PLAT-01`

Rationale:
- `TASK-P0-10` unlocks both the coaching chain and the rewritten UI contract path.
- `TASK-P0-12` matches the current branch focus (`feature/task-p0-12-evidence-plumbing`) and hardens evidence plumbing early.
- `TASK-P0-11` should follow once the payload contract is frozen.
- `TASK-P0-13` turns the desired coaching behavior into stable golden tests.
- `TASK-PLAT-01` removes the active WSL2/PowerShell planner friction as soon as the coaching chain is stabilized.

## Dispatch Guidance For This Repo
- Use repo-local `TASKS.md` as source of truth.
- Worker should update `TASKS.md` before closeout.
- Preferred dispatch format:

```text
repo=aimSoloAnalysis do task TASK-P0-10
```

- Preferred closeout format:

```text
TASK-P0-10 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Relevant Files
- `/home/cobra/aimSoloAnalysis/TASKS.md`
- `/home/cobra/aimSoloAnalysis/ARCHITECTURE.md`
- `/home/cobra/aimSoloAnalysis/docs/wsl2_native_js_ui_design.md`
