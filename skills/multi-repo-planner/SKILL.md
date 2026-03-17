---
name: multi-repo-planner
description: Centralized planning and dispatch for multiple active repositories using CENTRAL as the canonical task system, with a DB-backed source of truth as the target operating model. Use when coordinating 2+ repos, sequencing dependencies, converting design docs into CENTRAL-owned tasks, dispatching worker work, running portfolio reprioritization, and reconciling worker outcomes back into CENTRAL.
---

# Multi Repo Planner

## Operating Assumption

Design for scale early.

Expected trajectory:

- one planner AI becomes multiple planners
- one worker becomes multiple workers running concurrently
- dispatch throughput increases substantially once the dispatcher fleet is active

Planning decisions, task storage, dispatch contracts, and reconciliation workflows must be chosen for multi-planner, multi-worker operation rather than single-user convenience.

## Canonical Model

Target source of truth:

- CENTRAL-managed structured task records in a DB-backed system

Transitional surfaces:

- generated summaries such as `tasks.md`
- exported markdown task cards when useful for human review or worker handoff
- repo-local mirrors or intake notes when helpful

Do not assume markdown files are the long-term canonical task store.

## Planner Workflow
1. Collect canonical planner-owned task state from CENTRAL’s canonical task system.
2. Consult repo-local boards only for roadmap context, local intake, or drift checks.
3. Convert new design intake into CENTRAL-owned canonical tasks with explicit `target_repo` and dependencies.
4. Select highest-priority unblocked work using structured task data, not incidental file ordering.
5. Dispatch one task per worker unless a worker explicitly supports a queue.
6. Reconcile worker results back into CENTRAL canonical state first.
7. Refresh generated summaries and any optional mirrors.
8. Re-plan continuously based on blockers, failures, completions, and queue pressure.

## Dispatch Contract
- Minimal dispatch: `repo=CENTRAL do task <task_id>`
- The worker resolves the task from CENTRAL canonical state.
- `target_repo` inside the task determines where implementation work belongs.
- Dispatch one task per worker at a time unless the worker explicitly supports a queue.

## Worker Contract
- Optional kickoff line: `<task_id> | in_progress | ref: <branch-or-context>`
- **Required before closeout:** Run `scripts/build.sh` in the target repo. Task MUST fail if the build script fails. No exceptions.
- Required closeout line: `<task_id> | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>`
- Planner updates CENTRAL canonical state first, then any generated views or repo-local mirrors.
- If blocked, record exactly one concrete unblocker request.

## Build Script Discipline

Every repo must have a `scripts/build.sh` that exits non-zero on any failure. This is the single gate for "is this repo healthy?"

**Build script order (fail-fast):**
1. **Smoke test** — the critical-path "is it working?" check. Runs first.
2. **Full test suite** — all unit/integration tests.
3. **Build/compile** — type checks, bundling, etc.

If step 1 fails, steps 2-3 don't run. The smoke test is the first thing that breaks.

**Acceptance criteria for all coding tasks:** `scripts/build.sh` must exit 0. This is non-negotiable and automatically included in every task's testing requirements.

**Current build scripts:**
| Repo | Build script | Smoke test |
|------|-------------|------------|
| MOTO_HELPER | TBD | `pnpm test -- smoke` (CENTRAL-OPS-96) |
| AIM_SOLO_ANALYSIS | TBD | TBD |
| CENTRAL | TBD | TBD |

## Status Discipline
- Keep status values explicit and machine-queryable.
- Prefer lifecycle models that can support review and reconciliation states as scale increases.
- Keep no more than one actively claimed task per worker unless the worker model explicitly supports batching.
- Treat stale blocked or stale in-progress work as replanning candidates.

## Repo Registry Discipline

Before creating or retargeting any planner task, the target repo must be registered in the CENTRAL canonical registry. `task-create`, `task-update`, and `planner-new` all enforce this — they fail with a concrete error and a suggested `repo-onboard` command if the repo is not registered.

Required workflow:

1. Register the repo first: `python3 scripts/central_task_db.py repo-onboard --repo-id REPO_ID --repo-root /path/to/repo`
2. Verify identity: `python3 scripts/central_task_db.py repo-resolve --repo REPO_ID`
3. Create planner tasks only after the registry entry is confirmed.

Do not work around a registry error by inventing a new repo ID inside task JSON. The registry is the single source of truth for repo identity across tasks, health checks, and dispatching. See `docs/repo_registry_onboarding.md` for the full onboarding reference.

## Smoke Test Discipline

Every project must have exactly one "is it working?" smoke test. This is the north-star health signal for the project.

**Hard rules:**
- If a repo has no smoke test, the **first task** for that repo must create one.
- If a repo has no `scripts/build.sh`, create one — smoke test first, then full tests, then build.
- Every coding task's acceptance criteria includes `scripts/build.sh` exits 0. This is enforced by `planner-new` defaults.
- If the smoke test regresses, fixing it takes priority over all other work for that repo.

**What makes a good smoke test:**
- Tests the critical user-facing path end-to-end (e.g., "submit input → verify correct output").
- Uses test fixtures (test user, test data) — no dependency on real accounts or external state.
- Has a mocked variant (runs in CI, no API keys) and optionally a live variant (calls real LLM/APIs).
- Runs fast (< 30s) so it can gate every dispatch cycle.
- Fails loudly with a clear error message about what broke.

**Current smoke tests:**
| Repo | Smoke test command | Status |
|------|-------------------|--------|
| MOTO_HELPER | `pnpm test -- smoke` | CENTRAL-OPS-96 (in progress) |
| AIM_SOLO_ANALYSIS | TBD | Needs creation |

## New Project Intake
- Onboard any new repo in the CENTRAL registry before creating planner tasks targeting it.
- Add canonical CENTRAL tracking for any new repo or non-git project.
- Treat repo-local task boards as optional local views, not required execution truth.
- Track workflow-only or non-repo work as canonical CENTRAL tasks with explicit paths.
- **Create a smoke test task as the first task for any newly onboarded repo.**

## Design Intake Conversion
- Treat major design docs as intake, not executable work.
- Convert intake into CENTRAL-owned canonical tasks before dispatching implementation.
- Ensure each task includes objective, context, scope, deliverables, acceptance, and testing requirements.
- Keep repo-local mirrors optional.

## Dependency Discipline

Dependencies must be declared at task creation time, not patched in after the fact. Hidden sequencing in prose produces a broken dispatch graph.

**At creation:**
- Always pass `--depends-on <task_id>` (repeatable) when using `planner-new`.
- Declare `dependencies: [...]` in batch YAML items when using `task-batch-create`.

**After creation:**
- Run `dep-lint` to detect task IDs mentioned in text fields without a declared edge.
- Use `dep-graph` when planning a new work tranche to verify sequencing is encoded.
- Use `dep-show --task-id <id>` to inspect a single task's forward and reverse edges.

**Commands:**
```sh
python3 scripts/central_task_db.py dep-show --task-id CENTRAL-OPS-47
python3 scripts/central_task_db.py dep-graph
python3 scripts/central_task_db.py dep-lint
```

See [docs/dependency_discipline.md](../../docs/dependency_discipline.md) for the full discipline reference.

Use [references/dispatch-and-status.md](references/dispatch-and-status.md) for reusable planner snippets.
