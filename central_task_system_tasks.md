# CENTRAL Canonical Task System Task Packet

These tasks convert the portfolio from repo-local planning boards to a `CENTRAL`-owned canonical task system.

Core rule:

- tasks live in `CENTRAL`
- tasks are self-contained
- each task names the target repo for implementation work
- planner/coordinator owns task creation, update, sequencing, and closeout reconciliation
- user should rarely edit tasks manually

Preferred dispatch pattern:

```text
repo=CENTRAL do task CENTRAL-OPS-01
```

If a task explicitly targets another repo for implementation, the task body itself is still sourced from `CENTRAL`.

Closeout pattern:

```text
CENTRAL-OPS-01 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

---

## Task CENTRAL-OPS-01: Freeze canonical CENTRAL task schema and storage model

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Define the canonical task format and on-disk layout for all future planner-owned work in `CENTRAL`.

## Context
- Current state is mixed:
  - repo-local `tasks.md` boards
  - `CENTRAL/tasks.md` mirror
  - ad hoc task packet files for worker-ready detail
- User decision is now explicit:
  - `CENTRAL` is canonical
  - tasks must be self-contained
  - target repo is a field on the task, not the place the task lives

## Deliverables
1. Decide canonical storage layout in `CENTRAL`, for example:
   - one large `tasks.md`
   - `tasks/` directory with one file per task
   - hybrid index + per-task files
2. Define required task fields:
   - task id
   - status
   - target repo
   - objective
   - context
   - scope/boundaries
   - deliverables
   - acceptance
   - testing
   - dependencies
   - closeout contract
3. Define how summaries/indexes point to full task bodies.
4. Provide at least one canonical example task in the new format.

## Acceptance Criteria
1. A worker can execute a task from `CENTRAL` without needing a repo-local task file.
2. A planner can add/update tasks without ambiguity about where the canonical record lives.
3. The chosen layout supports growth across multiple repos without turning into a single unmanageable file.

## Testing
- Manual review of the schema doc and example task.
- Verify a sample dispatch can reference a `CENTRAL` task ID without external context.

## Notes
- This is the hard gate for the rest of the migration.
- Do not implement the whole migration here; define the contract cleanly first.

---

## Task CENTRAL-OPS-02: Update planner skills and dispatch contracts for CENTRAL-as-truth

## Repo
- Primary repo: `/home/cobra/.codex/skills`
- Secondary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Update planner/operator skills and central docs so they reflect `CENTRAL` as the canonical task system.

## Context
- Existing `multi-repo-planner` assumes repo-local task boards remain execution truth.
- That assumption is now wrong for the target operating model.

## Deliverables
1. Update `multi-repo-planner` to treat `CENTRAL` tasks as execution truth.
2. Update dispatch language so:
   - tasks are sourced from `CENTRAL`
   - repo is an explicit field on the task
   - repo-local boards are optional mirrors/reference only
3. Update any supporting references/runbooks in `CENTRAL`.
4. Define how other skills (`autonomy-planner`, `autonomy-operator`, `autonomy-triage`) should refer to CENTRAL-owned tasks when relevant.

## Acceptance Criteria
1. Skills no longer claim repo-local boards are the default source of truth.
2. Planner guidance clearly states that task creation/update is planner-owned in `CENTRAL`.
3. Skills and `CENTRAL` docs agree on the new task ownership model.

## Testing
```bash
python3 /home/cobra/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/cobra/.codex/skills/multi-repo-planner
```

## Notes
- This task is a docs/skill-contract change, not dispatcher implementation.
- Keep the wording explicit enough that future sessions behave correctly.

---

## Task CENTRAL-OPS-03: Re-root dispatcher operating model to CENTRAL-owned tasks

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `todo`

## Objective
- Define how the dispatcher and autonomy task system should consume or mirror `CENTRAL`-owned tasks instead of repo-local boards.

## Context
- Today the autonomy DB exists in `photo_auto_tagging`, but planning content still mostly originates elsewhere.
- Future model requires dispatcher work to start from `CENTRAL` task definitions and route execution into target repos.

## Deliverables
1. Define the integration model between `CENTRAL` tasks and autonomy DB tasks.
2. Decide whether autonomy DB becomes:
   - execution cache of `CENTRAL` tasks
   - canonical runtime state with `CENTRAL` as authored source
   - another arrangement with explicit synchronization rules
3. Document how task IDs map between `CENTRAL` and autonomy DB, if different.
4. Define how target repo, writable dirs, and execution policy are derived from a CENTRAL task.

## Acceptance Criteria
1. There is a clear answer to: “How does dispatcher know what work exists if tasks live in CENTRAL?”
2. The model is concrete enough to guide implementation without reopening the same architecture debate.
3. Repo targeting and execution policy routing are explicit.

## Testing
- Manual review and planner sign-off.
- Reference current dispatcher/autonomy surfaces and show how they would consume a CENTRAL task.

## Notes
- This is an architecture/design task, not the implementation of synchronization itself.

---

## Task CENTRAL-OPS-04: Implement CENTRAL-to-autonomy task ingestion bridge

## Repo
- Primary repo: `/home/cobra/photo_auto_tagging`
- Secondary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Implement the bridge that turns canonical `CENTRAL` tasks into dispatcher-consumable autonomy tasks without requiring manual duplication.

## Context
- Once the schema and integration model are frozen, the runtime needs an actual ingestion/sync path.

## Deliverables
1. Implement a command or script that reads canonical tasks from `CENTRAL`.
2. Create/update corresponding autonomy DB tasks with:
   - title
   - prompt/body
   - repo-root
   - dependencies
   - execution policy
3. Prevent duplicate task creation on repeated syncs.
4. Document sync direction and conflict behavior.

## Acceptance Criteria
1. A planner-authored task in `CENTRAL` can become a dispatchable autonomy task without manual re-entry.
2. Re-running the sync does not duplicate tasks.
3. The runtime can show eligible/pending tasks created from `CENTRAL`.

## Testing
```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
python -m autonomy.cli init --profile default
python -m autonomy.cli task list --json --status pending
```

## Notes
- Keep scope focused on the bridge, not the entire migration.

---

## Task CENTRAL-OPS-05: Migrate planner-owned active work into canonical CENTRAL tasks

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Convert the current planner-owned cross-repo work into canonical `CENTRAL` task records using the new format.

## Context
- Current state includes:
  - summary lines in `CENTRAL/tasks.md`
  - task packets
  - repo-local task boards
- The new system needs one canonical representation for active planner-owned work.

## Deliverables
1. Convert current planner-owned dispatch/system tasks into the canonical CENTRAL format.
2. Define which existing items are:
   - active canonical tasks
   - legacy references
   - archived/superseded
3. Update central indexes so they point to the canonical task bodies.

## Acceptance Criteria
1. Planner-owned active work is readable from `CENTRAL` alone.
2. There is no ambiguity about which copy of a planner-owned task is authoritative.
3. Legacy references are labeled clearly rather than silently left to drift.

## Testing
- Manual review of converted task records.
- Verify the central index points to the full task bodies cleanly.

## Notes
- Do not migrate every repo-local backlog item blindly.
- Focus first on planner-owned and cross-repo coordination tasks.

---

## Task CENTRAL-OPS-06: Define planner-owned closeout and reconciliation workflow

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `todo`

## Objective
- Define how worker results update canonical CENTRAL tasks and how optional repo-local mirrors are reconciled.

## Context
- User expectation is that the planner handles task updates and closeout, not the user.
- That requires a precise reconciliation workflow once workers finish work in other repos.

## Deliverables
1. Define closeout fields required from workers.
2. Define planner responsibilities after worker closeout:
   - update CENTRAL task status
   - record tests/ref
   - update portfolio summary
   - optionally mirror into repo-local boards if still maintained
3. Define blocked-task handling and unblocker recording.
4. Define when a repo-local board is updated versus ignored.

## Acceptance Criteria
1. Planner closeout work is explicit and repeatable.
2. User is not required to manually reconcile task state after workers finish.
3. The workflow fits both the current mixed system and the future CENTRAL-only system.

## Testing
- Manual review and planner sign-off.
- Validate that the workflow can consume worker closeout strings without ambiguity.

## Notes
- This is a process-contract task, not UI or DB implementation.

---

## Task CENTRAL-OPS-07: Retire repo-local boards as execution truth

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repos: all tracked project repos

## Status
- `todo`

## Objective
- Complete the transition so repo-local task boards are no longer treated as execution truth.

## Context
- This task should happen only after schema, skills, bridge, and reconciliation policy are in place.

## Deliverables
1. Mark repo-local boards as:
   - legacy mirror
   - reference only
   - archived
   depending on repo needs
2. Update docs and planner instructions accordingly.
3. Remove contradictory guidance that still claims repo-local execution truth.

## Acceptance Criteria
1. `CENTRAL` is clearly the execution truth in docs and practice.
2. Repo-local boards no longer drive dispatch decisions by default.
3. Remaining repo-local boards are labeled intentionally, not left ambiguous.

## Testing
- Manual review across `CENTRAL` docs and active planner skills.

## Notes
- This is the final migration/cleanup task, not the first one to dispatch.
