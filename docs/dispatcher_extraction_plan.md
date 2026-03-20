# Dispatcher Extraction Plan

**Date:** 2026-03-15
**Task:** CENTRAL-OPS-41
**Status:** extraction-ready (phase 1 complete)

## Objective

Move `central_runtime.py`, `dispatcher_control.py`, `central_task_db.py`, and their tests
out of CENTRAL into a dedicated dispatcher repo while keeping CENTRAL as the portfolio-level
health aggregator and planner surface.

---

## What moves to the dispatcher repo

| Item | Current path in CENTRAL | Notes |
|------|------------------------|-------|
| `central_runtime.py` | `scripts/central_runtime.py` | Daemon and worker bridge |
| `dispatcher_control.py` | `scripts/dispatcher_control.py` | Operator wrapper |
| `central_task_db.py` | `scripts/central_task_db.py` | DB access layer — see DB coupling note |
| DB migrations | `db/migrations/` | Schema owns the DB; moves with the task system |
| Reconcile test | `tests/test_central_runtime_reconcile.py` | |
| Worker status smoke | `tests/test_central_runtime_worker_status.sh` | |
| Task ID reservation tests | `tests/test_central_task_id_reservations.sh` | |
| Dispatcher task tests | `tests/test_central_task_repo_registry.py` | |
| Codex model tests | `tests/test_dispatcher_codex_model.py` | |
| Restart handoff tests | `tests/test_dispatcher_restart_handoff.py` | |
| Bootstrap doc | `docs/central_task_db_bootstrap.md` | Becomes dispatcher-local doc |

## What stays in CENTRAL

| Item | Notes |
|------|-------|
| `repo_health.py` | Portfolio health aggregator; dispatches probes to external repos |
| `tools/repo_health/` | Shared contract library; stays in CENTRAL |
| `skills/multi-repo-planner/` | Planner role; CENTRAL-owned |
| Planning task records | DB records created by planner; DB location is configurable |
| `docs/` other than bootstrap | Planner docs, roadmaps, architecture |

---

## DB coupling (the key decision)

`central_task_db.py` is used by both:
- The **planner** (CENTRAL) — creates tasks, marks done/blocked, generates summaries
- The **dispatcher** (runtime) — claims tasks, records lease/result

**Recommended model: DB stays at CENTRAL, dispatcher points to it via env var.**

```
CENTRAL_TASK_DB_PATH=/home/cobra/CENTRAL/state/central_tasks.db
```

The dispatcher reads/writes CENTRAL's DB over the filesystem. `central_task_db.py` moves
with the dispatcher repo (it is the DB access layer). CENTRAL invokes it as a subprocess
(`DB_SCRIPT` env var), exactly as it does today.

This preserves CENTRAL as the DB owner and planner surface. The dispatcher repo holds
the DB implementation; CENTRAL drives it via subprocess.

---

## Coupling points resolved (done)

### 1. `dispatcher_control.py` — `REPO_DIR` was hardcoded

**Before:**
```python
REPO_DIR = Path("/home/cobra/CENTRAL")
```

**After (landed):**
```python
REPO_DIR = Path(os.environ.get("CENTRAL_DISPATCHER_REPO_DIR", "/home/cobra/CENTRAL")).expanduser().resolve()
RUNTIME_SCRIPT = Path(os.environ.get("CENTRAL_DISPATCHER_RUNTIME_SCRIPT", str(REPO_DIR / "scripts" / "central_runtime.py")))
DB_SCRIPT = Path(os.environ.get("CENTRAL_DISPATCHER_DB_SCRIPT", str(REPO_DIR / "scripts" / "central_task_db.py")))
```

After extraction: set `CENTRAL_DISPATCHER_REPO_DIR` to the new dispatcher repo root.
Existing CENTRAL installs continue working with the default.

### 2. `repo_health.py` — `bootstrap_doc` pointed to CENTRAL `REPO_ROOT`, not dispatcher root

**Before:**
```python
bootstrap_doc=env_path(..., REPO_ROOT / "docs" / "central_task_db_bootstrap.md")
```

**After (landed):**
```python
bootstrap_doc=env_path(..., repo_root / "docs" / "central_task_db_bootstrap.md")
```

After extraction: the bootstrap doc lives in the dispatcher repo's `docs/`. The health
adapter will find it via `--dispatcher-root` or `CENTRAL_DISPATCHER_ROOT`.

---

## Coupling points already handled by repo_health.py

`repo_health.py` already supports the extracted-repo model via env vars:

| Env var | Purpose |
|---------|---------|
| `CENTRAL_DISPATCHER_ROOT` | Dispatcher repo root for all path resolution |
| `CENTRAL_DISPATCHER_CONTROL_SCRIPT` | Override `dispatcher_control.py` path |
| `CENTRAL_DISPATCHER_RUNTIME_SCRIPT` | Override `central_runtime.py` path |
| `CENTRAL_DISPATCHER_DB_SCRIPT` | Override `central_task_db.py` path |
| `CENTRAL_DISPATCHER_RECONCILE_TEST` | Override reconcile test path |
| `CENTRAL_DISPATCHER_WORKER_STATUS_SMOKE` | Override worker status smoke path |
| `CENTRAL_DISPATCHER_BOOTSTRAP_DOC` | Override bootstrap doc path |

No changes needed in the health adapter to support an external dispatcher repo.

---

## Remaining coupling in `central_runtime.py`

`central_runtime.py` is already portable:
- Uses `REPO_ROOT = SCRIPT_PATH.parent.parent` (auto-resolved, not hardcoded)
- Imports `central_task_db` from `sys.path` (sibling script, moves with it)
- `AUTONOMY_ROOT = Path(os.environ.get("CENTRAL_AUTONOMY_ROOT", str(REPO_ROOT.parent / "Dispatcher")))` — env-overridable, defaults to `../Dispatcher` relative to the CENTRAL repo root. **Do not hardcode a path here.**

---

## After-extraction operator setup

When the dispatcher repo is created and checked out at `~/central-dispatcher/`:

```bash
# Point CENTRAL at the external dispatcher
export CENTRAL_DISPATCHER_ROOT=/home/cobra/central-dispatcher

# Point dispatcher at CENTRAL's DB
export CENTRAL_TASK_DB_PATH=/home/cobra/CENTRAL/state/central_tasks.db

# repo_health.py picks up CENTRAL_DISPATCHER_ROOT automatically
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo dispatcher

# dispatcher_control.py picks up CENTRAL_DISPATCHER_REPO_DIR
export CENTRAL_DISPATCHER_REPO_DIR=/home/cobra/central-dispatcher
python3 /home/cobra/central-dispatcher/scripts/dispatcher_control.py status
```

---

## Ownership boundary

| Concern | Owner |
|---------|-------|
| Task schema and DB file | CENTRAL (DB lives in `CENTRAL/state/`) |
| DB access layer (`central_task_db.py`) | Dispatcher repo |
| Worker dispatch and lease lifecycle | Dispatcher repo |
| Health aggregation and probing | CENTRAL (`repo_health.py`) |
| Planner role and task creation | CENTRAL |
| Portfolio dashboards, reports | CENTRAL |

---

## Migration phases

**Phase 1 (done):** Extraction-prep — remove hardcoded CENTRAL paths from dispatcher
scripts so they can run from any repo root with env var overrides.

**Phase 2:** Create `central-dispatcher` repo. Copy the files listed in "What moves"
above. Add `CENTRAL_DISPATCHER_REPO_DIR`, `CENTRAL_DISPATCHER_RUNTIME_SCRIPT`, and
`CENTRAL_TASK_DB_PATH` to the operator launch environment.

**Phase 3:** Smoke — run `repo_health.py snapshot --repo dispatcher
--dispatcher-root /home/cobra/central-dispatcher` and verify all checks pass.

**Phase 4 (optional cleanup):** Remove migrated scripts from CENTRAL. Keep thin
forwarding stubs if any operator scripts invoke them by path directly.

---

## Acceptance criteria

- [x] `dispatcher_control.py` picks up repo root from env var, no hardcoded CENTRAL path
- [x] `repo_health.py` dispatcher adapter does not embed CENTRAL `REPO_ROOT` in any path
- [x] All dispatcher adapter paths are overridable via env vars (pre-existing)
- [ ] Phase 2: dispatcher repo created and checked out
- [ ] Phase 3: health adapter smoke passes against external dispatcher root
- [ ] Phase 4: CENTRAL cleaned of migrated scripts (optional)
