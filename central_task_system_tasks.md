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
- `done`

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
- Reconciliation result under `CENTRAL-OPS-13`:
  - closed as superseded by `CENTRAL-OPS-18`
  - no bootstrap task file exists; keep this packet entry only as historical planning context

---

## Task CENTRAL-OPS-06: Define planner-owned closeout and reconciliation workflow

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

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
- Reconciliation result under `CENTRAL-OPS-13`:
  - closed as completed planning-contract work
  - later DB-native implementation belongs to `CENTRAL-OPS-15` and `CENTRAL-OPS-19`

---

## Task CENTRAL-OPS-07: Retire repo-local boards as execution truth

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repos: all tracked project repos

## Status
- `done`

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
- Reconciliation result under `CENTRAL-OPS-13`:
  - closed as superseded by `CENTRAL-OPS-19`
  - do not dispatch separately under the DB-canonical plan

---

## Task CENTRAL-OPS-08: Harden canonical task schema for machine parsing, prioritization, and DB extensibility

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Transitional note:
  - this task now exists only to support bootstrap compatibility and migration from markdown
  - it is not the end-state architecture after `CENTRAL-OPS-09`
- The resulting contract was useful for the transitional `CENTRAL-OPS-03` and `CENTRAL-OPS-04` markdown bridge.
- Bootstrap task file:
  - [`tasks/CENTRAL-OPS-08.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-08.md)

---

## Task CENTRAL-OPS-09: Redesign CENTRAL canonical task system around SQLite as source of truth

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Manual review complete on 2026-03-10:
  - DB schema doc reviewed
  - high-level canonical task model reviewed
  - DB-native autonomy integration model reviewed

## Notes
- This supersedes markdown-as-canonical assumptions from earlier bootstrap work.
- Completed in:
  - [`docs/central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
  - [`docs/central_task_system.md`](/home/cobra/CENTRAL/docs/central_task_system.md)
  - [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
  - [`tasks/CENTRAL-OPS-09.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-09.md)

---

## Task CENTRAL-OPS-10: Define multi-planner and multi-worker concurrency model for dispatcher scale

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Manual review complete on 2026-03-10 using [`docs/central_task_concurrency.md`](/home/cobra/CENTRAL/docs/central_task_concurrency.md).

## Notes
- This is a scaling-design task, not a runtime implementation task.
- Completed in:
  - [`docs/central_task_concurrency.md`](/home/cobra/CENTRAL/docs/central_task_concurrency.md)
  - [`tasks/CENTRAL-OPS-10.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-10.md)

---

## Task CENTRAL-OPS-11: Design DB-native CENTRAL/autonomy integration and retire markdown-first bridge assumptions

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

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
- Manual review complete on 2026-03-10 using [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md).

## Notes
- This is the architecture reset required before deepening bridge implementation.
- Completed in:
  - [`docs/central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
  - [`docs/central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
  - [`tasks/CENTRAL-OPS-11.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-11.md)

---

## Task CENTRAL-OPS-12: Define generated views and operator surfaces for DB-canonical task management

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

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
- Completed in:
  - [`tasks/CENTRAL-OPS-12.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-12.md)
  - [`docs/central_generated_views.md`](/home/cobra/CENTRAL/docs/central_generated_views.md)

---

## Task CENTRAL-OPS-13: Reconcile and re-scope transitional CENTRAL-OPS-05 through CENTRAL-OPS-08 under the DB-canonical model

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Clean up the remaining transitional CENTRAL planning tasks so `CENTRAL-OPS-05` through `CENTRAL-OPS-08` accurately reflect what is still needed, what is superseded, and what should be treated as migration-only work.

## Context
- `CENTRAL-OPS-09` through `CENTRAL-OPS-12` changed the long-term architecture materially.
- Earlier bootstrap tasks still contain markdown-first assumptions, stale status text, or ambiguous scope.
- Before implementation starts, the planner backlog needs to stop carrying contradictory work items.

## Deliverables
1. Review `CENTRAL-OPS-05` through `CENTRAL-OPS-08` against the DB-canonical architecture.
2. Mark each task as still needed, superseded, completed, or transitional-only.
3. Rewrite any remaining task text so it matches the DB-canonical direction.
4. Reconcile statuses and notes consistently across bootstrap task files, `tasks.md`, and `central_task_system_tasks.md`.

## Acceptance Criteria
1. No remaining `CENTRAL-OPS-05` through `CENTRAL-OPS-08` task contradicts DB-canonical planning.
2. Summary surfaces and bootstrap task files agree on current status and scope.
3. The implementation tranche can proceed without ambiguity about which bootstrap tasks are still relevant.

## Testing
- Manual review of `tasks.md`.
- Manual review of `central_task_system_tasks.md`.
- Manual review of the affected bootstrap task files.
- Manual review complete on 2026-03-10:
  - `CENTRAL-OPS-05` through `CENTRAL-OPS-08` classified and reconciled
  - summary/index and packet statuses aligned
  - `CENTRAL-OPS-08` retained only as transitional compatibility work

## Notes
- This is the cleanup gate before implementation.
- Completed in:
  - [`tasks/CENTRAL-OPS-13.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-13.md)
  - [`tasks/CENTRAL-OPS-08.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-08.md)
- Bootstrap task file:
  - [`tasks/CENTRAL-OPS-13.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-13.md)

---

## Task CENTRAL-OPS-14: Implement the canonical CENTRAL SQLite task database and migration scaffold

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Create the real CENTRAL SQLite task database, migrations, and bootstrap tooling so planner truth can move out of markdown and into structured storage.

## Context
- `CENTRAL-OPS-09` defined the DB-canonical architecture.
- `CENTRAL-OPS-10` and `CENTRAL-OPS-11` defined concurrency and runtime integration expectations.
- No actual canonical DB implementation exists yet in `CENTRAL`.

## Deliverables
1. Create the SQLite schema and migration files for the canonical CENTRAL task DB.
2. Add a bootstrap/init command that creates or upgrades the DB safely.
3. Add minimal repo/config plumbing so tools can locate the canonical DB reliably.
4. Document how the DB is initialized and where it lives.

## Acceptance Criteria
1. A fresh CENTRAL checkout can initialize the canonical task DB with one command.
2. The implemented schema matches the DB design docs closely enough for later CRUD and runtime work.
3. Schema upgrades are handled by explicit migrations rather than ad hoc replacement.

## Testing
- Initialize the DB in a clean or temporary location.
- Verify the expected tables exist.
- Run the migration command twice and confirm idempotent behavior.
- Manual verification complete on 2026-03-10 using:
  - `python3 scripts/central_task_db.py init --db-path /tmp/central_tasks_test.db --json`
  - `python3 scripts/central_task_db.py status --db-path /tmp/central_tasks_test.db --json`
  - `python3 scripts/central_task_db.py init --json`
  - `python3 scripts/central_task_db.py status --json`

## Notes
- This is the real start of DB-native implementation.
- Completed in:
  - [`tasks/CENTRAL-OPS-14.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-14.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`db/migrations/0001_initial.sql`](/home/cobra/CENTRAL/db/migrations/0001_initial.sql)
  - [`docs/central_task_db_bootstrap.md`](/home/cobra/CENTRAL/docs/central_task_db_bootstrap.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-14.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-14.md)

---

## Task CENTRAL-OPS-15: Implement planner-facing DB CRUD and reconciliation commands

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Implement planner-facing commands or APIs that create, update, prioritize, assign, and reconcile canonical tasks directly in the CENTRAL DB.

## Context
- The DB foundation must exist before planner workflows can stop relying on markdown edits.
- Planner-owned operations need clear boundaries from runtime-owned operations.
- This is the control plane for future planner AI usage.

## Deliverables
1. Create planner-facing task create/update commands or APIs against the CENTRAL DB.
2. Implement dependency management, priority updates, owner assignment, and status transitions for planner lifecycle.
3. Implement planner-side closeout reconciliation commands for done/blocked outcomes.
4. Document command usage for planner operation.

## Acceptance Criteria
1. A planner can create and modify canonical tasks without editing markdown files.
2. Planner lifecycle state, dependencies, and ownership can be updated through structured commands.
3. Closeout reconciliation can be performed against the DB without manual SQL.

## Testing
- Create a test task in the DB.
- Update its priority, dependencies, and ownership.
- Reconcile a closeout outcome and verify DB state changes as expected.
- Manual review complete on 2026-03-10:
  - planner-facing DB CRUD and reconciliation commands added in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - command usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- This is the planner control plane task.
- Completed in:
  - [`tasks/CENTRAL-OPS-15.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-15.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-15.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-15.md)

---

## Task CENTRAL-OPS-16: Implement DB-generated operator views and exports

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Build the CLI/JSON/dashboard read models and any optional markdown exports that surface CENTRAL DB task state without becoming a second source of truth.

## Context
- `CENTRAL-OPS-12` defined the generated-view contract.
- Operators need real surfaces for summary, eligible, blocked, assignments, review, and task detail.
- These surfaces must read from DB state, not hand-maintained files.

## Deliverables
1. Implement required CLI and JSON views for summary, eligible, blocked, per-repo, assignments, review, and task detail.
2. Implement optional markdown export generation only where useful, clearly marked non-canonical.
3. Add freshness and non-canonical markers to generated outputs.
4. Document how operators regenerate or query these views.

## Acceptance Criteria
1. Operators can answer the key portfolio and queue questions from DB-generated views.
2. Generated outputs are clearly marked non-canonical.
3. The system does not require a giant manually maintained `tasks.md` to operate.

## Testing
- Populate sample DB records and verify each required view renders correctly.
- Verify freshness and source banners appear in generated outputs.
- Verify optional markdown exports can be regenerated from DB state.
- Manual review complete on 2026-03-10:
  - DB-generated operator views and markdown exports implemented in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - operator command usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- Generated views are implementation now, not just design.
- Completed in:
  - [`tasks/CENTRAL-OPS-16.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-16.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-16.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-16.md)

---

## Task CENTRAL-OPS-17: Implement DB-native dispatcher and runtime state integration

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

## Objective
- Implement the DB-native runtime path for dispatcher discovery, task claim and lease management, heartbeats, stale-lease recovery, and runtime status transitions.

## Context
- `CENTRAL-OPS-10` and `CENTRAL-OPS-11` defined the concurrency and integration model.
- The old markdown bridge is transitional and should not define steady-state runtime behavior.
- Dispatcher/runtime logic now needs a concrete DB-native execution path.

## Deliverables
1. Implement DB-native eligibility queries for dispatcher use.
2. Implement atomic claim and lease creation, heartbeat renewal, and stale-lease recovery.
3. Implement runtime status transitions including review, failure, timeout, and done handling.
4. Document how dispatcher/runtime actions interact with planner-owned state.

## Acceptance Criteria
1. Dispatcher can discover and claim eligible work from DB-native state without markdown file discovery.
2. Double-claim protection and stale-lease handling work according to the concurrency contract.
3. Runtime state transitions are queryable from structured DB tables.

## Testing
- Simulate eligible task discovery and claim flow.
- Simulate heartbeat renewal and stale lease recovery.
- Simulate runtime transitions into running, pending review, failed, timeout, and done.
- Manual review complete on 2026-03-10:
  - DB-native runtime eligibility, claim, heartbeat, transition, and stale-recovery commands added in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - runtime command usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- This is the runtime execution-plane implementation task.
- Completed in:
  - [`tasks/CENTRAL-OPS-17.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-17.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-17.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-17.md)

---

## Task CENTRAL-OPS-18: Migrate bootstrap CENTRAL task records into the canonical DB

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Import or migrate the current bootstrap CENTRAL task records into the canonical DB so live planning can stop depending on markdown-maintained state.

## Context
- The DB exists only once `CENTRAL-OPS-14` lands.
- Current task definitions and summaries still live in bootstrap markdown surfaces.
- Migration must preserve task identity and enough history to keep planning continuity.

## Deliverables
1. Implement a migration or import path from bootstrap CENTRAL task files and relevant packet surfaces into the DB.
2. Preserve stable `task_id` values and key metadata during migration.
3. Record migration provenance so imported records can be audited.
4. Document the migration procedure and rollback considerations.

## Acceptance Criteria
1. Existing bootstrap CENTRAL tasks appear in the canonical DB with stable IDs.
2. Migration can be audited and does not silently duplicate task records.
3. Planning can begin reading live task state from the DB after migration.

## Testing
- Run migration against a representative bootstrap task set.
- Verify stable IDs and critical fields in DB output.
- Re-run migration and confirm duplicate-safe behavior.
- Manual review complete on 2026-03-10:
  - bootstrap import from task files and packet-only records implemented as `migrate-bootstrap` in [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - migration and rollback usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- This is the cutover import task.
- Completed in:
  - [`tasks/CENTRAL-OPS-18.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-18.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-18.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-18.md)

---

## Task CENTRAL-OPS-19: Retire markdown-first bridge paths and non-canonical manual task maintenance

## Repo
- Primary repo: `/home/cobra/CENTRAL`
- Secondary repo: `/home/cobra/photo_auto_tagging`

## Status
- `done`

## Objective
- Finish the transition away from markdown-first task management by retiring bridge-first assumptions, stopping manual canonical markdown maintenance, and leaving only DB-native operation plus optional generated exports.

## Context
- The DB, planner CRUD, generated views, dispatcher integration, and migration need to exist first.
- Transitional bootstrap tools should not become permanent architecture.
- This task is the cutover and cleanup point.

## Deliverables
1. Retire `autonomy central sync` or equivalent markdown-first paths as primary workflow.
2. Update docs and skills so DB-native planning and runtime operation are the canonical path.
3. Remove or demote any remaining manual canonical markdown maintenance expectations.
4. Preserve optional import/export or archival tooling only where still useful.

## Acceptance Criteria
1. DB-native planning and dispatch are the documented primary workflow.
2. Operators are no longer expected to maintain canonical task state in markdown.
3. Transitional bridge paths are clearly marked deprecated, retired, or import-only.

## Testing
- Manual review of updated docs and skill surfaces.
- Verify primary operator and planner commands point at DB-native workflow.
- Verify optional export/import paths remain clearly non-canonical.
- Manual review complete on 2026-03-10:
  - canonical autonomy docs and packaged skills updated to point at CENTRAL DB-native workflow
  - `autonomy central sync` demoted to deprecated import-only status in docs and CLI help
  - remaining manual canonical markdown maintenance expectations removed from skill/runbook surfaces

## Notes
- This is the final cutover and cleanup task for the migration phase.
- Completed in:
  - [`tasks/CENTRAL-OPS-19.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-19.md)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
  - [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
  - [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
  - [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
  - [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
  - [`/home/cobra/.codex/skills/autonomy-planner/SKILL.md`](/home/cobra/.codex/skills/autonomy-planner/SKILL.md)
  - [`/home/cobra/.codex/skills/autonomy-operator/SKILL.md`](/home/cobra/.codex/skills/autonomy-operator/SKILL.md)
  - [`/home/cobra/.codex/skills/autonomy-triage/SKILL.md`](/home/cobra/.codex/skills/autonomy-triage/SKILL.md)
  - [`/home/cobra/photo_auto_tagging/autonomy/cli.py`](/home/cobra/photo_auto_tagging/autonomy/cli.py)
  - [`/home/cobra/photo_auto_tagging/autonomy/central_sync.py`](/home/cobra/photo_auto_tagging/autonomy/central_sync.py)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-19.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-19.md)

---

## Task CENTRAL-OPS-20: Implement CENTRAL-native dispatcher daemon loop

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add a generated board-style landing page so operators can scan canonical CENTRAL tasks in markdown form without hand-maintaining task-board truth.

## Context
- `CENTRAL-OPS-16` provided DB-generated views and basic markdown exports.
- `CENTRAL-OPS-19` made DB-native operation the primary workflow and retired manual canonical markdown maintenance.
- A generated landing page is still useful for human scanning, but it must remain non-canonical and DB-driven.

## Deliverables
1. Add a CLI command that exports a generated `tasks.md`-style landing page from DB state.
2. Include a clear generated/non-canonical banner.
3. Include portfolio summary and canonical task listings.
4. Document the export command for operator use.

## Acceptance Criteria
1. Operators can regenerate a board-style landing page from DB state with one command.
2. The landing page is clearly marked as generated and non-canonical.
3. The export does not restore manual markdown maintenance as planner truth.

## Testing
- Manual review of the new export command.
- Manual review of the updated CLI documentation.
- Manual review complete on 2026-03-10:
  - `export-tasks-board-md` added to [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - export usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- This is a generated-view extension task, not a return to markdown-canonical planning.
- Completed in:
  - [`tasks/CENTRAL-OPS-20.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-20.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-20.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-20.md)

---

## Task CENTRAL-OPS-21: Implement CENTRAL-native worker execution bridge

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add a one-shot export command that regenerates the standard markdown export bundle from CENTRAL DB state.

## Context
- Individual markdown exports already exist for summaries and task cards.
- `CENTRAL-OPS-20` added a generated board-style landing page.
- Operators still benefit from a single bundle-refresh command for all standard markdown outputs.

## Deliverables
1. Add a bundle export command.
2. Generate board, summary, blocked, review, assignments, and task-card outputs.
3. Keep all bundle outputs non-canonical and generated from DB state.
4. Document the new command.

## Acceptance Criteria
1. One command regenerates the standard markdown bundle.
2. Generated outputs remain clearly non-canonical.
3. The bundle command does not depend on markdown as input.

## Testing
- Manual review of the bundle export implementation.
- Manual review of the updated CLI documentation.
- Manual review complete on 2026-03-10:
  - `export-markdown-bundle` added to [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - bundle export usage documented in [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)

## Notes
- This is an operator convenience layer over existing DB-native export surfaces.
- Completed in:
  - [`tasks/CENTRAL-OPS-21.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-21.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-21.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-21.md)

---

## Task CENTRAL-OPS-22: Cut over dispatcher launcher and operator workflow to CENTRAL-native runtime

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add DB-native per-repo markdown exports for repo-specific operator scans and sharing.

## Context
- Per-repo queue views were part of the generated-surface contract.
- The bundle export now exists, but repo-specific markdown files were still missing.
- Operators may still want repo-specific markdown exports as long as they remain generated and non-canonical.

## Deliverables
1. Add a per-repo markdown export command.
2. Extend the markdown bundle to emit per-repo files.
3. Keep repo exports clearly non-canonical.
4. Document the command surface.

## Acceptance Criteria
1. Operators can generate `generated/per_repo/<repo_id>.md` from DB state.
2. The bundle command also emits per-repo files.
3. Per-repo exports do not become canonical planner state.

## Testing
- Manual review of the per-repo export implementation.
- Manual review of the updated CLI documentation.
- Minimal smoke verification complete on 2026-03-10:
  - `python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-repo-md --repo-id CENTRAL --json`

## Notes
- This is a generated-export extension task, not a workflow change.
- Completed in:
  - [`tasks/CENTRAL-OPS-22.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-22.md)
  - [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- Canonical bootstrap file:
  - [`tasks/CENTRAL-OPS-22.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-22.md)


---

## Task CENTRAL-OPS-23: Generate DB-native task-board landing page export

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add a DB-generated landing-page export so operators can read a `tasks.md`-style portfolio board without reintroducing manual canonical markdown maintenance.

## Testing
- Manual review complete on 2026-03-10 for generated board export implementation and CLI docs.

## Notes
- Completed in [`tasks/CENTRAL-OPS-23.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-23.md).

---

## Task CENTRAL-OPS-24: Add one-shot markdown export bundle generation

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add a one-shot command that regenerates the standard non-canonical markdown export bundle from CENTRAL DB state.

## Testing
- Manual review complete on 2026-03-10 for bundle export implementation and CLI docs.

## Notes
- Completed in [`tasks/CENTRAL-OPS-24.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-24.md).

---

## Task CENTRAL-OPS-25: Add per-repo markdown export generation

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add DB-native per-repo markdown export generation so operators can refresh repo-specific queue views directly from CENTRAL DB state.

## Testing
- Manual review complete on 2026-03-10 for per-repo export implementation and CLI docs.

## Notes
- Completed in [`tasks/CENTRAL-OPS-25.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-25.md).

---

## Packet Correction For CENTRAL-OPS-20 Through CENTRAL-OPS-25

- The inline packet numbering for `CENTRAL-OPS-20` through `CENTRAL-OPS-25` drifted during the export-task tranche.
- Canonical bootstrap task files and [`tasks.md`](/home/cobra/CENTRAL/tasks.md) are the authoritative references for current numbering and status.
- Current canonical mapping is:
  - `CENTRAL-OPS-20`: [`tasks/CENTRAL-OPS-20.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-20.md)
  - `CENTRAL-OPS-21`: [`tasks/CENTRAL-OPS-21.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-21.md)
  - `CENTRAL-OPS-22`: [`tasks/CENTRAL-OPS-22.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-22.md)
  - `CENTRAL-OPS-23`: [`tasks/CENTRAL-OPS-23.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-23.md)
  - `CENTRAL-OPS-24`: [`tasks/CENTRAL-OPS-24.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-24.md)
  - `CENTRAL-OPS-25`: [`tasks/CENTRAL-OPS-25.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-25.md)

---

## Task CENTRAL-OPS-26: Add CENTRAL-native runtime self-check command

## Repo
- Primary repo: `/home/cobra/CENTRAL`

## Status
- `done`

## Objective
- Add a deterministic CENTRAL-native self-check that validates the daemon and worker bridge against temporary isolated state.

## Testing
- Minimal smoke verification complete on 2026-03-10:
  - `python3 /home/cobra/CENTRAL/scripts/central_runtime.py self-check`

## Notes
- Completed in:
  - [`tasks/CENTRAL-OPS-26.md`](/home/cobra/CENTRAL/tasks/CENTRAL-OPS-26.md)
  - [`scripts/central_runtime.py`](/home/cobra/CENTRAL/scripts/central_runtime.py)
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
