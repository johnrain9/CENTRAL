# Dispatch System Task Packet

These are worker-ready planner tasks for the autonomy dispatcher/control-plane work.

Dispatch rule:

- give one task at a time unless the worker explicitly supports a queue
- preferred handoff:

```text
repo=photo_auto_tagging do task AUT-OPS-01
```

Closeout rule:

```text
AUT-OPS-01 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

---

## Task AUT-OPS-01: Install and expose canonical `autonomy` CLI

## Repo
- Primary repo: `/home/cobra/photo_auto_tagging`
- Secondary touchpoint: `/home/cobra/CENTRAL` only if operator docs need a matching update

## Status
- `done`

## Objective
- Make `autonomy ...` the real shell-visible operator command instead of relying on `python -m autonomy.cli ...` from an activated repo venv.

## Context
- The implementation already exists in `photo_auto_tagging/autonomy`.
- `pyproject.toml` already defines console scripts for `autonomy` and `autonomy-cli`.
- On this machine, `.venv/bin/autonomy` is not currently present, so the practical path is the module fallback.

## Deliverables
1. Fix the repo environment so the `autonomy` console script exists in `/home/cobra/photo_auto_tagging/.venv/bin/`.
2. Document the supported operator bootstrap path.
3. Decide whether shell exposure should happen via:
   - editable install only
   - `.zshrc` helper/wrapper
   - both
4. Ensure the chosen path does not require re-explaining repo-local bootstrapping each session.

## Acceptance Criteria
1. `source /home/cobra/photo_auto_tagging/.venv/bin/activate && command -v autonomy` succeeds.
2. `autonomy --help` works from the prepared runtime.
3. `autonomy dispatch status --profile default` works after init.
4. Docs clearly state the supported bootstrap/install path and no longer imply a command that is missing locally.

## Testing
```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
command -v autonomy
autonomy --help
autonomy init --profile default
autonomy dispatch status --profile default
```

## Notes
- Do not break existing `python -m autonomy.cli ...` fallback behavior.
- Prefer the smallest change that makes the canonical command real.
- CENTRAL implementation:
  - [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py) now prefers `.venv/bin/autonomy` and falls back to `python -m autonomy.cli`.
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md) now documents the editable-install bootstrap path and supported shell contract.

---

## Task AUT-OPS-02: Define planner-owned task ingestion into autonomy DB

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

## Objective
- Define and document how planner-owned work moves from repo-local markdown boards into autonomy DB tasks, including who owns updates and when markdown stops being the primary execution surface.

## Context
- Current planning still lives mainly in repo-local `tasks.md` / `TASKS.md` files plus `CENTRAL/tasks.md`.
- User expectation: the planner should create/update these tasks, not the user.
- The autonomy DB exists but is not yet the default planning surface.

## Deliverables
1. A documented ingestion workflow for converting planner intent into autonomy tasks.
2. Rules for task ownership:
   - planner-owned task creation/update
   - worker closeout expectations
   - repo-local board sync responsibilities
3. Clear source-of-truth rules during the transition period.
4. Example command sequences for:
   - create task
   - set dependencies
   - promote to pending
   - reconcile completion back to central tracking

## Acceptance Criteria
1. A new operator/planner can follow the doc without asking where to put a new task.
2. The doc distinguishes:
   - bootstrap markdown tasks
   - autonomy DB tasks
   - central portfolio tracking
3. The workflow explicitly supports planner-driven creation and update without relying on the user to edit task files manually.

## Testing
```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
python -m autonomy.cli task list --json --status pending
python -m autonomy.cli task eligible --json
python -m autonomy.cli task blocked --json
```

## Notes
- This is a planning/documentation task first, not a full migration implementation.
- Keep it concrete enough that a later task can automate the flow.
- Completed in [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md) under `Planner-Owned Ingestion Workflow`.

---

## Task AUT-OPS-03: Update autonomy skills and docs for real local bootstrap

## Repo
- Primary repo: `/home/cobra/photo_auto_tagging`
- Secondary repo: `/home/cobra/.codex/skills`
- Mirror/update repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Align autonomy skill docs and repo docs with the actual local runtime path, including init requirements, repo venv expectations, and the `dispatcher` launcher.

## Context
- Current skill text assumes the canonical `autonomy` CLI exists.
- Actual working flow today depends on `photo_auto_tagging/.venv` and `python -m autonomy.cli`, plus the new `dispatcher` wrapper in `CENTRAL`.

## Deliverables
1. Update skill docs:
   - `autonomy-operator`
   - `autonomy-planner`
   - `autonomy-triage`
2. Update repo docs under:
   - `/home/cobra/photo_auto_tagging/docs/autonomy_skills/`
3. Document:
   - first-run `init`
   - current runtime command path
   - `dispatcher` wrapper usage
   - when to use `autonomy ...` vs module fallback
4. Mirror any packaged-skill updates into `CENTRAL` if that remains your sync policy.

## Acceptance Criteria
1. Skill docs do not claim a command path that fails locally.
2. Repo docs and packaged skills agree on the operator bootstrap path.
3. `dispatcher` is mentioned as the normal user/operator start path where appropriate.

## Testing
```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
python -m autonomy.cli --help
python -m autonomy.cli dispatch status --profile default
```

## Notes
- Keep docs explicit about current reality, not idealized future state.
- If `autonomy` console-script installation is fixed first, docs may describe that as preferred and module path as fallback.
- CENTRAL mirror/update completed in [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md).
- External docs updated:
  - `/home/cobra/.codex/skills/autonomy-*`
  - `/home/cobra/photo_auto_tagging/docs/autonomy_skills/*`

---

## Task AUT-OPS-04: Add pending-review and retry operating runbook

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

## Objective
- Create the operator runbook for `pending_review`, failure triage, retry, reject, approve, and stale-review clearing.

## Context
- The dispatcher/runtime exists, but a control plane without a crisp review rhythm will accumulate stale review debt.
- The relevant surfaces already exist:
  - `report review-aging`
  - `report failures`
  - `worker inspect`
  - `worker tail`
  - task approval/rejection/reset commands

## Deliverables
1. Review cadence doc:
   - what to check daily / per dispatch cycle
2. Decision matrix for:
   - approve
   - reject
   - retry/reset
   - leave blocked
3. Required evidence on closeout:
   - tests
   - commit/ref
   - blocker statement if blocked
4. Examples using the actual CLI commands.

## Acceptance Criteria
1. An operator can process stale review backlog without guessing.
2. Retry vs reject vs approve rules are explicit enough to be repeatable.
3. The doc references the actual CLI/reporting surfaces already in the system.

## Testing
```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
python -m autonomy.cli report review-aging --json --profile default
python -m autonomy.cli report failures --json --profile default
python -m autonomy.cli worker list --json --profile default
```

## Notes
- This is primarily an operational-doc task.
- Keep it auditable and deterministic.
- Completed in [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md) under `Review And Retry Runbook`.

---

## Task AUT-OPS-05: Define source-of-truth migration from markdown boards to autonomy DB

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

## Objective
- Decide and document the migration path from repo markdown task boards to autonomy DB-backed planning/execution.

## Context
- Today:
  - repo-local boards still drive most planning
  - `CENTRAL/tasks.md` is the portfolio mirror
  - autonomy DB exists but is not primary
- Future target:
  - autonomy DB becomes primary execution surface
  - markdown boards become mirror/bootstrap/archive as needed

## Deliverables
1. Migration phases with explicit cutover rules.
2. Source-of-truth policy per phase.
3. Drift-resolution policy between:
   - repo-local board
   - central board
   - autonomy DB
4. Rollback plan if the autonomy DB workflow proves incomplete.
5. Suggested sequence of follow-on implementation tasks.

## Acceptance Criteria
1. The migration plan clearly states what system is authoritative at each phase.
2. Planner/operator responsibilities are explicit during transition.
3. The plan is concrete enough to drive later implementation tasks without reopening the same design debate.

## Testing
- Manual review and planner sign-off.
- Verify the doc references current reality in:
  - `/home/cobra/CENTRAL/tasks.md`
  - `/home/cobra/CENTRAL/dispatch_system_readme.md`
  - `/home/cobra/photo_auto_tagging/docs/autonomy_skills/*`

## Notes
- This is a design/planning task, not a schema/code migration task.

---

## Task AUT-OPS-06: Move autonomy docs out of photo_auto_tagging and make CENTRAL canonical

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`
- Secondary repo: `/home/cobra/.codex/skills`

## Status
- `todo`

## Objective
- Re-home autonomy operator/planner/triage documentation so `CENTRAL` is the canonical location and `photo_auto_tagging` no longer carries the long-term source of truth for dispatch-system docs.

## Context
- Autonomy runtime implementation currently lives in `/home/cobra/photo_auto_tagging/autonomy`.
- Some autonomy docs were updated under `/home/cobra/photo_auto_tagging/docs/autonomy_skills/*`.
- User direction is explicit: these docs do not belong in PhotoQuery as canonical documentation and should move to `CENTRAL`.

## Deliverables
1. Create or update the canonical autonomy docs in `CENTRAL`.
2. Move or rewrite autonomy-skill repo docs so `CENTRAL` is the canonical reference location.
3. Reduce `photo_auto_tagging/docs/autonomy_skills/*` to one of:
   - stub/reference docs pointing to `CENTRAL`
   - intentionally minimal implementation-local notes
   - removal if no longer needed
4. Ensure packaged skills under `/home/cobra/.codex/skills/autonomy-*` reference the CENTRAL-owned docs/runbooks where appropriate.
5. Update any README or operator references that still imply PhotoQuery owns the dispatch documentation set.

## Acceptance Criteria
1. `CENTRAL` clearly contains the canonical autonomy docs/runbooks.
2. `photo_auto_tagging` no longer appears to be the long-term doc home for autonomy operations/planning.
3. Skills/docs/README references do not point users to stale PhotoQuery doc paths as primary references.
4. Repo-local cleanup does not remove implementation-specific information that still belongs next to the code.

## Testing
```bash
rg -n "autonomy_skills|dispatch_system_readme|CENTRAL" /home/cobra/CENTRAL /home/cobra/photo_auto_tagging/docs /home/cobra/.codex/skills
git -C /home/cobra/CENTRAL diff --stat
git -C /home/cobra/photo_auto_tagging diff --stat
```

## Notes
- This is a documentation ownership cleanup task, not a runtime-code change.
- Keep implementation docs near code only when they are truly implementation-local; move operator/planner truth to `CENTRAL`.
- Completed in [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md) under `Source-Of-Truth Migration`.
