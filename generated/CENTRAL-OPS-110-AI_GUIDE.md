# AI Guide: photo_auto_tagging

This file is a draft for `/Users/paul/projects/photo_auto_tagging/AI_GUIDE.md`.

It was validated against the cloned repo's implementation on 2026-03-21, but the current sandbox cannot write into that repo, so the guide was staged here as an artifact.

## Purpose

`photo_auto_tagging` is a local-first AI media workspace with two main product surfaces:

- `photoquery`: photo indexing, semantic retrieval, review, quality, keeper, discovery, metadata writing, and UI.
- `genflow`: generation orchestration for local image/video workflows and service supervision around tools like ComfyUI and `video_queue`.

The old autonomous worker runtime does not live here anymore. The repo README points operators to the standalone Dispatcher repo instead.

Use this guide to orient quickly before changing code.

## Read First

Open these files first:

1. `README.md`
2. `pyproject.toml`
3. `photoquery/cli/main.py`
4. `photoquery/services.py`
5. `genflow/cli.py`

Then branch by task area:

- indexing/search/review/metadata: `photoquery/`
- generation orchestration and service control: `genflow/`
- product/design intent and rollout notes: `docs/`
- machine-local sync and operator helpers: `scripts/`

## Entry Points

CLI entry points are declared in `pyproject.toml`:

- `photoquery = photoquery.cli.main:app`
- `pq = photoquery.cli.main:app`
- `genflow = genflow.cli:app`

Primary operator commands:

- `photoquery index`, `photoquery run`, `photoquery test`, `photoquery ui`
- `photoquery concept-*`, `quality-*`, `keeper-*`, `write`, `reindex`, `status`
- `genflow setup init`, `genflow up`, `genflow down`, `genflow status`, `genflow restart`, `genflow ui`

## Repo Shape

```text
docs/                      design docs, audits, rollout notes, runbooks
genflow/                   generation orchestration package
photoquery/                main photo indexing/search/review application
scripts/                   sync/watch helpers and systemd units
backups/                   local scratch/output area
PLAN.md                    planning notes
tasks.md                   task backlog/history
```

High-signal code areas:

- `photoquery/cli/main.py`: Typer command surface and UI launch path
- `photoquery/services.py`: main command implementation facade; currently the biggest backend concentration point
- `photoquery/service_domains/`: smaller wrappers for cluster, gallery, workspace, quality, keeper, indexing, automation, and status behavior
- `photoquery/storage/db.py`: SQLite schema, migrations, connection helpers, WAL setup
- `photoquery/indexer/`: filesystem scan, metadata probe, crops, embeddings, model identity
- `photoquery/search/`: flat-index build/load, ranking, query parsing
- `photoquery/review/`: queueing, decisions, review policy, persistence
- `photoquery/metadata/`: ExifTool integration and write path
- `photoquery/ui/app.py`: legacy Gradio UI, still live and feature-bearing
- `photoquery/ui/api/`: FastAPI routes mounted into the UI app
- `photoquery/ui/v2/`: SvelteKit frontend for review/gallery/discover/workspace/settings surfaces
- `genflow/cli.py`: Typer command surface for runtime control
- `genflow/core/`: config, command bus, setup, library/gallery/evaluation helpers, operation log, backup/model cache
- `genflow/runtime/`: supervisor, admission control, resource broker, telemetry, workflow routing
- `genflow/ui/app.py`: Gradio-based GenFlow UI

## How `photoquery` Works

`photoquery` is local-first. The main runtime assumptions are:

- config at `~/.config/photoquery/config.toml`
- data at `~/.local/share/photoquery/`
- SQLite DB at `~/.local/share/photoquery/photoquery.sqlite`
- logs at `~/.local/share/photoquery/logs/photoquery.log`
- lock file at `~/.local/share/photoquery/photoquery.lock`

Important config defaults from `photoquery/config.py` and `docs/sample_config.toml`:

- index roots default to `~/Pictures`
- active embedding model defaults to `clip` `v1`
- crops are enabled by default
- metadata write mode defaults to XMP sidecars
- workflow run mode defaults to `balanced`

The broad execution loop is:

1. scan configured roots
2. probe metadata and optionally generate person crops
3. compute embeddings for the active model
4. build or refresh the flat index
5. retrieve candidates for text queries or slug-driven workflows
6. review/rate/train concept, quality, and keeper models
7. optionally write metadata, export learnings, or promote outputs downstream

Important implementation facts:

- `photoquery/services.py` is still the main execution owner at about 8.5k lines
- `photoquery/service_domains/` exists, but the full service split is not complete yet
- changing embedding model family/version requires reindexing
- crop-first retrieval is supported through `image_crops` plus view resolution helpers
- metadata writes are audit-oriented and sidecar-first
- keeper mode can run against the main index or a separate keeper data directory

## UI Surfaces

`photoquery ui` launches the legacy Gradio app from `photoquery/ui/app.py`, then mounts additional FastAPI routes during lifespan startup:

- `/api/...` from `photoquery/ui/api/`
- `/gallery/...` from `photoquery/ui/gallery/routes.py`
- `/v2` static SPA if `photoquery/ui/v2/build/` exists

If the V2 frontend has not been built yet, `photoquery ui` serves a `503` message on `/v2` explaining that the static build is missing.

V2 is a real code path, not a placeholder. `photoquery/ui/v2/src/routes/` currently includes:

- `review`
- `gallery`
- `discover`
- `workspace`
- `search`
- `metrics`
- `settings`

The checked-in `photoquery/ui/v2/README.md` is still the default Svelte scaffold and should not be treated as product documentation.

## How `genflow` Works

`genflow` is a local orchestration layer, not a hosted backend. It manages per-user profiles under `~/.genflow/profiles/<profile>/` unless `GENFLOW_CONFIG` or `GENFLOW_USER_HOME` overrides that behavior.

Key runtime directories from `genflow/core/config.py`:

- profile config: `~/.genflow/profiles/<profile>/config.toml`
- profile data: `~/.genflow/profiles/<profile>/data/`
- profile logs: `~/.genflow/profiles/<profile>/logs/`
- profile cache: `~/.genflow/profiles/<profile>/cache/`
- supervisor state: `~/.genflow/profiles/<profile>/runtime/supervisor_state.json`

Default managed or checked services:

- `comfyui`
- `video_queue`
- `video_wall`
- `photoquery` as a health-check target

`genflow ui` optionally calls `RuntimeSupervisor.up()` before launching the Gradio UI. The supervisor owns start/stop/status/restart, PID tracking, health polling, and log placement under the profile.

Use `genflow/core/setup.py` when working on bootstrap or environment validation. `setup init` both writes starter config and runs a health report over writable dirs and configured integration URLs.

## Docs Map

`docs/` is large and worth navigating intentionally.

Best starting points by topic:

- repo/product orientation: `README.md`, `PLAN.md`
- `photoquery` architecture and workflows:
  - `docs/photoquery_ai_slug_concept_and_decision_architecture.md`
  - `docs/photoquery_concept_review_and_training_main_lld.md`
  - `docs/photoquery_semantic_clustering_and_discovery_design.md`
  - `docs/keeper_isolated_index_design.md`
  - `docs/photoquery_ui_v2_spec.md`
- `genflow` architecture and operations:
  - `docs/genflow_design.md`
  - `docs/genflow_runtime_orchestration_design.md`
  - `docs/genflow_workflow_routing_design.md`
  - `docs/genflow_operator_runbook.md`
- current maintenance state:
  - `docs/photoquery_refactor_analysis_20260321.md`
  - `docs/services_split_plan.md`
  - `docs/known_limitations.md`

Do not treat `docs/autonomy_skills/` as canonical for dispatcher behavior. That directory explicitly says the autonomy docs moved to `CENTRAL`.

## Scripts

`scripts/` is mostly workstation automation, not core application logic.

Current scripts are primarily:

- photoquery sync/watch helpers: `pq_sync_*`
- model pipeline supervision helpers: `pq_model_pipeline_*`
- matching `systemd/` service and timer units

If your task is about business logic, start in `photoquery/` or `genflow/`, not `scripts/`.

## Current State And Hotspots

The repo is active and not fully decomposed yet.

Important current-state notes:

- `photoquery` remains the center of gravity
- `photoquery/services.py` and `photoquery/ui/app.py` are both very large and still live
- the V2 frontend is real, but the legacy Gradio UI is still the main launch surface
- `service_domains/` indicates an ongoing backend split, not a completed architecture transition
- the docs tree contains many design and rollout documents; prefer the newer dated docs when multiple versions exist

## Testing And Validation

There is substantial automated coverage under:

- `photoquery/tests/`
- `genflow/tests/`

In this environment, a targeted pytest run failed before execution because `numpy` is not installed in the available Python environment, so implementation validation for this guide was done by direct code inspection instead of runnable tests.

## Working Heuristics For AI Agents

Use this sequence unless the task is unusually narrow:

1. Read `README.md`
2. Open the relevant CLI entry point
3. Open the relevant backend module
4. Open one or two matching tests
5. Only then fan out into design docs

Practical guidance:

- prefer code over older design docs if they disagree
- check whether a flow exists in legacy Gradio UI, V2 UI, or both before changing UI behavior
- assume local filesystem paths and SQLite state are the source of truth
- treat `services.py` edits carefully because many workflows still converge there
- treat `genflow` as profile-scoped and machine-local, not multi-user SaaS infrastructure
