# Repo Health — Operator Guide

CENTRAL provides two primary commands for monitoring repo health across the tracked repository set.

---

## Quick reference

```
# Instant view from DB (no live checks, fast)
python3 scripts/repo_health.py latest

# Drill into one repo from DB
python3 scripts/repo_health.py latest --repo dispatcher

# Collect fresh health data and display it
python3 scripts/repo_health.py snapshot --skip-smoke

# Collect, display, AND persist to DB for future `latest` reads
python3 scripts/repo_health.py snapshot --persist

# Single-repo live snapshot
python3 scripts/repo_health.py snapshot --repo aimSoloAnalysis

# JSON output (both commands support --json)
python3 scripts/repo_health.py latest --json
python3 scripts/repo_health.py snapshot --json
```

---

## Commands

### `latest` — instant DB read

Reads the most recently persisted snapshot for each repo from the CENTRAL DB.
**No live checks are run.** Response is immediate.

```
python3 scripts/repo_health.py latest [--repo REPO_ID] [--json]
```

| Flag | Description |
|------|-------------|
| `--repo REPO_ID` | Restrict to one repo and show full check breakdown |
| `--json` | Emit JSON array of snapshot rows (includes `report_json`) |

Output shows `STALE` in the freshness column when a snapshot has exceeded its TTL (default 1 hour).

### `snapshot` — live collection

Runs all probes for the registered repos and renders results.
With `--persist`, also writes the collected bundle to the DB so `latest` has current data.

```
python3 scripts/repo_health.py snapshot [--repo REPO_ID] [--persist] [--skip-smoke] [--json]
```

| Flag | Description |
|------|-------------|
| `--repo REPO_ID` | Collect only this repo (repeatable) |
| `--persist` | Write results to CENTRAL DB after collection |
| `--ttl-seconds N` | Freshness TTL in seconds for the persisted record (default 3600) |
| `--skip-smoke` | Skip the dispatcher self-check (faster, no side effects) |
| `--command-timeout N` | Timeout per probe command in seconds (default 60) |
| `--json` | Emit JSON bundle instead of the operator table |

---

## Reading the output

### Status markers

| Marker | Meaning |
|--------|---------|
| `[PASS]` | Check passed — healthy |
| `[WARN]` | Check degraded but not failing |
| `[FAIL]` | Check failed — needs attention |
| `[UNKN]` | Status could not be determined |
| `[N/A ]` | Check not applicable for this repo profile |

The **Overall** line in `snapshot` output uses `HEALTHY / DEGRADED / FAILING / UNKNOWN`.

### Freshness

`latest` shows `fresh` or `STALE` per snapshot based on how old the record is relative to its TTL.
If any snapshots are stale, the command prints a remediation hint at the bottom.

### Evidence quality

`strong` — multiple live command probes all passed
`partial` — some probes passed, some failed or produced no output
`weak` — most probes failed; evidence is based on file presence only
`none` — no usable evidence could be gathered

---

## Registered repos

| Repo ID | Profile | Root |
|---------|---------|------|
| `dispatcher` | automation | `/home/cobra/CENTRAL` |
| `aimSoloAnalysis` | application | `/home/cobra/aimSoloAnalysis` |
| `motoHelper` | application | `/home/cobra/motoHelper` |

---

## Habitual workflow

1. **Daily glance**: `python3 scripts/repo_health.py latest` — zero-cost, instant.
2. **Before dispatching work**: `python3 scripts/repo_health.py latest --repo aimSoloAnalysis` to confirm the target is healthy.
3. **After a sprint or deploy**: `python3 scripts/repo_health.py snapshot --persist` to refresh all records.
4. **Automation / CI**: use `--json` and parse `working_status` / `evidence_quality` per repo.

---

## DB-level access

The underlying snapshots are in `state/central_tasks.db` table `repo_health_snapshots`.

```
# View latest per-repo summary from DB directly
python3 scripts/central_task_db.py health-snapshot-latest

# View history (default 20 records)
python3 scripts/central_task_db.py health-snapshot-history --limit 10

# Filter to one repo
python3 scripts/central_task_db.py health-snapshot-latest --repo-id dispatcher
```
