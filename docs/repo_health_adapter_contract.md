# Repo Health Adapter Contract

This contract keeps multi-repo health onboarding honest and machine-readable.

## Canonical Repo Report

Every adapter emits one JSON object with:

- `schema_version = "repo-health.v2"`
- `generated_at`
- `repo`
- `summary`
- `checks`
- `coverage`
- `evidence`
- optional `metadata`

`repo` must include:

- `repo_id`
- `display_name`
- `repo_root`
- `adapter_name`
- `adapter_version`
- `profile`

`summary` must include:

- `working_status`
- `evidence_quality`
- `overall_status`
- `headline`
- `counts`

## Canonical Checks

Each profile keeps the same baseline check IDs:

- `workspace`
- `dependencies`
- `tests`
- `build`
- `runtime`

Extra checks such as `smoke`, `queue`, or `lint` are allowed, but the canonical baseline checks may not be omitted.

Allowed check statuses:

- `pass`
- `warn`
- `fail`
- `unknown`
- `not_applicable`

Interpretation:

- `pass`: a concrete probe or document supports the claim and the surface is healthy.
- `warn`: the surface exists and is partially evidenced, but a caveat matters.
- `fail`: the evidence says the surface is broken.
- `unknown`: CENTRAL cannot honestly claim the state yet.
- `not_applicable`: the surface truly does not exist for that repo.

## Coverage

Coverage is a first-class object, not an implied check:

```json
{
  "status": "measured",
  "measured_percent": 84.5,
  "summary": "Measured line coverage is 84.5%.",
  "evidence_ids": ["coverage-measured"]
}
```

Allowed coverage statuses:

- `measured`
- `coverage_unknown`
- `not_applicable`

Rules:

- `measured` requires a real numeric percentage and evidence.
- `coverage_unknown` is required when coverage is not measured yet.
- Do not imply coverage from test files or artifact presence alone.

## Evidence

Evidence is a flat shared list. Checks and coverage reference it by `evidence_ids`.

Every evidence item must include:

- `evidence_id`
- `kind`
- `source`
- `summary`
- `observed_at`

Allowed `kind` values:

- `file`
- `command`
- `service`
- `note`

Any `pass`, `warn`, or `fail` check must reference evidence.

## Working Status vs Evidence Quality

- `working_status` answers: is the repo working based on the declared contract surfaces?
- `evidence_quality` answers: how complete is the current evidence set?

Derivation rules:

- mandatory `fail` => `working_status=fail`
- mandatory `unknown` => `working_status=unknown`
- optional `warn` or `unknown` can keep `working_status=warn`
- mandatory `unknown` => `evidence_quality=unknown`
- `coverage_unknown` or optional `unknown` => `evidence_quality=warn`

## Helpers

- validator and builders: `tools/repo_health/contract.py`
- stub/validate CLI: `python3 -m tools.repo_health.cli`
- starter template: `tools/repo_health/adapter_template.py`
