# Portfolio Management Improvements

## Operating Model (Recommended)
- Keep repo-local `tasks.md` as execution source of truth.
- Use `/home/cobra/CENTRAL/tasks.md` as the portfolio mirror and planning board.
- Run a short sync at start/end of each work block to import status changes.

## Cadence
- Daily (5-10 min): refresh central board, identify blockers, pick max 1 `in_progress` per repo.
- Weekly (20-30 min): reprioritize across repos by impact and dependencies.
- Release checkpoint: verify all `in_progress` tasks have closeout notes and tests listed.

## Prioritization Rubric
Score each active task 1-5 on:
- User impact
- Unblock value (does it unlock other tasks?)
- Risk reduction
- Effort (inverse score: lower effort = higher score)

Use total score to order dispatch candidates.

## De-duplication Rules
- If tasks overlap across repos, keep one canonical owner task and mark linked duplicates as `shadow` in central notes.
- Canonical owner should be the repo where code changes land.
- Cross-repo dependencies should be explicit: `depends_on: repo/task-id`.

## Dispatch Contract
- Planner dispatch: `repo=<repo_name> do task <task_id>`.
- Worker closeout: `<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>`.
- Worker must update target repo `tasks.md` status before closeout.

## Central File Structure Upgrades (Next)
- Add a `Now` section with top 3 tasks across all repos.
- Add `Blocked` section with one-line unblocker request per task.
- Add `Recently Done` section for last 7 days.
- Add owner field (`owner: me|ai-worker|unassigned`) for each active task.

## Automation Ideas
- Add `sync_tasks.sh` in `CENTRAL` to re-import statuses from all tracked repos.
- Add `detect_duplicates.sh` to flag similar task titles across repos.
- Add `stale_in_progress.sh` to flag tasks in `in_progress` for >3 days.
- Optional: generate a tiny HTML dashboard from `CENTRAL/tasks.md` for quick scanning.

## Expansion Model for New Repos
- New repo onboarding checklist:
  - Ensure repo has `tasks.md` with task ID + status fields.
  - Add repo path to central tracked list.
  - Import first snapshot and compute status counts.
- Tag each task with a short area label (`backend`, `ui`, `ops`, `infra`, `data`) for cross-repo filtering.
