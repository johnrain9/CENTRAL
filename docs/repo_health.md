# Repo Health

`scripts/repo_health.py` is the CENTRAL operator entrypoint for multi-repo health snapshots.

## Operator Command

Run the initial repo set:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --json
CENTRAL_DISPATCHER_ROOT=/path/to/dispatcher-repo python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo dispatcher --json
```

Restrict the snapshot to one or more repos:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo dispatcher --json
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo aimSoloAnalysis --json
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo motoHelper --json
```

The human view answers two questions for every repo:

- current `working_status`
- current `evidence_quality`

Coverage is always explicit:

- `measured` with a real percentage
- `coverage_unknown` when no trustworthy measurement exists yet
- `not_applicable` only when coverage genuinely does not apply

## Bundle Shape

The aggregate command emits a bundle with:

- `schema_version = "repo-health-bundle.v1"`
- `generated_at`
- `summary`
- `repos`
- optional `metadata`

`summary` contains:

- `repo_count`
- `working_status`
- `evidence_quality`
- `overall_status`

Each repo entry is a canonical repo-health report from `tools/repo_health/contract.py`.

## Initial Repos

- `dispatcher`
  adapter that uses live dispatcher/runtime/DB probes plus dispatcher unittest and smoke commands.
  It can run against an extracted runtime repo by setting:
  - `CENTRAL_DISPATCHER_ROOT`
  - `CENTRAL_DISPATCHER_CONTROL_SCRIPT`
  - `CENTRAL_DISPATCHER_RUNTIME_SCRIPT`
  - `CENTRAL_DISPATCHER_DB_SCRIPT`
  - `CENTRAL_DISPATCHER_RECONCILE_TEST`
  - `CENTRAL_DISPATCHER_WORKER_STATUS_SMOKE`
  - `CENTRAL_DISPATCHER_BOOTSTRAP_DOC`
- `aimSoloAnalysis`
  CENTRAL wrapper around `/home/cobra/aimSoloAnalysis/tools/repo_health_adapter.py` plus CENTRAL-owned workspace/runtime metadata.
- `motoHelper`
  CENTRAL wrapper around `/home/cobra/motoHelper/tools/repo_health_adapter.py` plus CENTRAL-owned workspace/runtime metadata.

## Extracted Dispatcher Ownership Boundary

The dispatcher/runtime control plane is a peer service to CENTRAL:

- CENTRAL keeps canonical planning state, task priority/reconciliation, and repo-level health aggregation.
- the dispatcher repo owns runtime daemon code, runtime state bootstrap behavior, and live execution validation scripts.
- CENTRAL keeps answering `is the dispatcher working?` through this adapter by probing that peer repo's control surfaces.

When the dispatcher moves out of CENTRAL, keep `CENTRAL_DISPATCHER_ROOT` and the optional script overrides in place so health evidence stays central-aggregated while ownership is explicit.

## Honest Status Rules

- `pass`: the surface has concrete evidence and is healthy enough for the repo's current contract.
- `warn`: the surface is real and evidenced, but there is a caveat or a missing optional probe.
- `fail`: a required working surface failed according to concrete evidence.
- `unknown`: CENTRAL cannot honestly claim the surface yet; use this for absent tests, absent smoke probes, or coverage that is not measured.
- `not_applicable`: the surface truly does not exist for that repo.

`working_status` rolls up the canonical checks. `evidence_quality` answers how complete the evidence set is, with mandatory unknowns pushing it to `unknown` and `coverage_unknown` or optional unknowns keeping it visible as a weaker signal.
