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
- `done`

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
- Chosen layout:
  - [`tasks.md`](/home/cobra/CENTRAL/tasks.md) remains the summary index
  - [`tasks/CENTRAL-OPS-01.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-01.md) demonstrates the canonical per-task file format
- Canonical schema doc:
  - [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
- Reusable template:
  - [`tasks/TASK_TEMPLATE.md`](/home/cobra/CENTRAL/tasks/TASK_TEMPLATE.md)

---

## Task CENTRAL-OPS-02: Update planner skills and dispatch contracts for CENTRAL-as-truth

## Repo
- Primary repo: `/home/cobra/.codex/skills`
- Secondary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Completed in:
  - [`tasks/CENTRAL-OPS-02.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-02.md)
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
  - [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
  - [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
  - [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)

---

## Task CENTRAL-OPS-03: Re-root dispatcher operating model to CENTRAL-owned tasks

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

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
- Completed in:
  - [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
  - [`tasks/CENTRAL-OPS-03.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-03.md)

---

## Task CENTRAL-OPS-04: Implement CENTRAL-to-autonomy task ingestion bridge

## Repo
- Primary repo: `/home/cobra/photo_auto_tagging`
- Secondary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Completed in:
  - [`tasks/CENTRAL-OPS-04.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-04.md)
  - `/home/cobra/photo_auto_tagging/autonomy/central_sync.py`
  - `/home/cobra/photo_auto_tagging/autonomy/cli.py`

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

---

## Task CENTRAL-OPS-08: Harden canonical task schema for machine parsing, prioritization, and DB extensibility

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Refine the canonical CENTRAL task schema so it is both human-maintainable and reliably machine-ingestable for the future CENTRAL-to-autonomy bridge and SQLite-backed runtime state.

## Context
- `CENTRAL-OPS-01` froze the first canonical human-readable schema.
- That schema still needs hardening for automatic ingestion, dispatch ordering, explicit ownership semantics, and extensible mapping into runtime storage.
- Runtime execution state is expected to live in the autonomy system, likely backed by SQLite, while authored planner truth remains in `CENTRAL`.

## Deliverables
1. Define a strict machine-readable metadata contract for canonical task files.
2. Clarify `Planner Owner` and `Worker Owner` semantics and allowed values.
3. Add canonical fields for priority/dispatch ordering and timestamps.
4. Decide whether additional review or reconciliation lifecycle states are required.
5. Document how canonical task metadata maps into runtime/autonomy DB fields and how optional future fields extend safely.
6. Update the reusable task template accordingly.

## Acceptance Criteria
1. A bridge implementation can parse canonical metadata without ad hoc markdown heuristics.
2. Dispatch order can be derived from task metadata rather than file ordering.
3. Ownership semantics are explicit enough that planner assignment and worker assignment cannot be confused.
4. The schema can grow without breaking existing task files.

## Testing
- Manual review of `docs/central_task_system.md`.
- Manual review of `tasks/TASK_TEMPLATE.md`.
- Demonstrate at least one canonical task instance matches the hardened schema.

## Notes
- This is a schema-hardening task, not the bridge implementation itself.
- The resulting contract should feed directly into `CENTRAL-OPS-03` and `CENTRAL-OPS-04`.
- Canonical file:
  - [`tasks/CENTRAL-OPS-08.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-08.md)

---

## Task CENTRAL-OPS-09: Redesign CENTRAL canonical task system around SQLite as source of truth

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Redefine the CENTRAL task architecture so the canonical source of truth is a SQLite database rather than markdown task files.

## Context
- The markdown task-file model was useful for bootstrap but does not scale cleanly to hundreds of tasks, multiple planners, or high-throughput dispatch.
- The user’s explicit direction is that CENTRAL should not depend on markdown or flat files as the canonical store.
- Planner truth, dependency edges, assignment state, and lifecycle metadata need structured storage from the start.

## Deliverables
1. Define the canonical SQLite schema for CENTRAL-owned tasks.
2. Define which markdown surfaces, if any, remain as generated views or exports.
3. Define migration rules from current markdown task files into DB records.
4. Update the high-level architecture docs to make DB-canonical planning explicit.

## Acceptance Criteria
1. The canonical source of truth is unambiguously the DB, not markdown files.
2. The schema supports hundreds of tasks with indexed queries and dependency traversal.
3. The migration path from current bootstrap markdown is concrete.

## Testing
- Manual review of the revised architecture docs.
- Demonstrate that every required task field has a DB home.

## Notes
- This supersedes markdown-as-canonical assumptions from earlier bootstrap work.

---

## Task CENTRAL-OPS-10: Define multi-planner and multi-worker concurrency model for dispatcher scale

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Define claim, assignment, scheduling, and reconciliation rules that remain correct when multiple planner AIs and multiple workers operate concurrently.

## Context
- The target operating model includes multiple planners scheduling work for multiple workers around the clock.
- A single-user or single-planner task model will break under that load.
- Concurrency, locking, ownership, queue fairness, and stale-claim recovery must be designed intentionally.

## Deliverables
1. Define planner vs worker write responsibilities.
2. Define claim/lease semantics for workers and planners.
3. Define conflict rules for concurrent planner edits.
4. Define stale claim, retry, timeout, and reassignment handling.
5. Define dispatch fairness or prioritization policy across repos and worker capacity.

## Acceptance Criteria
1. The model prevents double-dispatch and ambiguous ownership.
2. Planner concurrency rules are concrete enough to implement safely.
3. Recovery from abandoned work is defined.

## Testing
- Manual review of concurrency scenarios and failure cases.
- Walk through at least three races: double claim, planner conflict, stale worker lease.

## Notes
- This is a scaling-design task, not a runtime implementation task.

---

## Task CENTRAL-OPS-11: Design DB-native CENTRAL/autonomy integration and retire markdown-first bridge assumptions

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `todo`

## Objective
- Replace the long-term architecture assumption that CENTRAL markdown syncs into autonomy, and define the DB-native integration model instead.

## Context
- `CENTRAL-OPS-03` and `CENTRAL-OPS-04` produced a valid transitional markdown-first bridge.
- That bridge should not define the steady-state system once CENTRAL moves to DB-canonical planning.
- The integration model now needs to start from CENTRAL structured task records.

## Deliverables
1. Define whether autonomy uses CENTRAL DB directly, syncs from it, or shares a unified schema.
2. Define task/state mapping between CENTRAL planning state and autonomy runtime state.
3. Define API or CLI boundaries for planner actions vs dispatcher actions.
4. Define how existing markdown-bridge behavior is deprecated or retired.

## Acceptance Criteria
1. The steady-state integration model starts from DB-canonical CENTRAL state, not markdown file discovery.
2. Planner/runtime separation remains clear.
3. Transitional bridge behavior is explicitly marked as temporary.

## Testing
- Manual review of integration options and selected model.
- Demonstrate how a newly created canonical DB task becomes dispatchable.

## Notes
- This is the architecture reset required before deepening bridge implementation.

---

## Task CENTRAL-OPS-12: Define generated views and operator surfaces for DB-canonical task management

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `todo`

## Objective
- Define which human-facing views should be generated from the canonical DB so operators and planners can scan the portfolio without making those views the source of truth.

## Context
- Once DB is canonical, flat files should become derived views, not manually maintained records.
- Operators still need readable summaries, dashboards, and possibly exported task cards for worker handoff.

## Deliverables
1. Define required generated views such as portfolio summary, per-repo queue, blocked tasks, and worker assignments.
2. Define whether `tasks.md` remains as a generated artifact or is replaced by another operator surface.
3. Define any exported task-card format for workers when a human-readable handoff is useful.
4. Define refresh/update rules for generated views.

## Acceptance Criteria
1. Operators can scan task state without editing generated surfaces manually.
2. Generated views are clearly non-canonical.
3. The design supports hundreds of tasks without requiring people to read a giant flat file.

## Testing
- Manual review of proposed views and refresh model.
- Demonstrate that critical operator questions can be answered from generated views.

## Notes
- Generated views must serve humans without becoming a second source of truth.
