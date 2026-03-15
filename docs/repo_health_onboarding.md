# Repo Health Onboarding

Use this path when adding another repo to CENTRAL's health surface.

## Flow

1. Pick the nearest profile: `application`, `automation`, `service_only`, or `library`.
2. Generate a stub with `python3 -m tools.repo_health.cli stub`.
3. Copy `tools/repo_health/adapter_template.py` into the target adapter location.
4. Replace unknowns with evidence-backed checks one surface at a time.
5. Add a real coverage percentage if a tool emits one; otherwise keep `coverage_unknown`.
6. Validate the adapter before registering it in `scripts/repo_health.py`.

## Minimum Honest Adapter

An onboarding pass is acceptable when:

- repo metadata is correct
- all canonical baseline checks exist
- each `pass`, `warn`, or `fail` check points to evidence
- coverage is either a measured percentage or `coverage_unknown`
- missing smoke or runtime probes stay `unknown`, not silently omitted

## Validation

```bash
python3 -m tools.repo_health.cli validate /home/cobra/CENTRAL/tools/repo_health/examples/central_adapter.py --json
```

The validator checks:

- required top-level fields
- canonical baseline checks for the selected profile
- status enums
- evidence linkage
- explicit coverage semantics
- missing smoke or runtime probes stay `unknown`, not silently omitted

## Registering the Adapter in CENTRAL

After validating a repo-local adapter, wire it into CENTRAL aggregation:

1. Implement the repo runner in `scripts/repo_health.py` and include it in `build_registry()`.
2. Confirm the runner returns canonical `repo-health.v2` checks and evidence IDs, including explicit `unknown`/`coverage_unknown` where honest.
3. If the repo exposes service checks, ensure runtime and queue/smoke evidence is sourced from live probes instead of docs-only assertions.
4. Verify single-repo output:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot --repo <repo-id> --json
```

5. Verify consolidated output:

```bash
python3 /home/cobra/CENTRAL/scripts/repo_health.py snapshot
```
