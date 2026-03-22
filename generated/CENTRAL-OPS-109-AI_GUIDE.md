# AI Guide: video_queue

This file is a draft for `/Users/paul/projects/video_queue/AI_GUIDE.md`.

It was validated against the cloned repo's implementation on 2026-03-21, but the current sandbox can only write inside `CENTRAL`, so the guide is staged here as an artifact.

## Purpose

`video_queue` is the local queue manager repo cloned from `github.com/johnrain9/comfy_frontend`.

Today it is a hybrid Python + frontend app that does four things:

- serves a FastAPI backend for workflow discovery, queue control, prompt generation, and status/log APIs
- stores queue state in a local SQLite database under `data/`
- runs a background worker that submits prompt JSON to ComfyUI and records results
- serves two browser UIs:
  - legacy static UI at `/` from `static/index.html`
  - current Svelte UI at `/v2` from `ui/build` when the frontend has been built

The core runtime is still Python-first. The V2 frontend is a client of the same backend APIs, not a separate service.

## Read First

Open files in this order:

1. `README.md`
2. `app.py`
3. `worker.py`
4. `db.py`
5. `defs.py`
6. `prompt_builder.py`
7. `workflow_defs_v2/*.yaml`
8. `ui/src/routes/+page.svelte`
9. `ui/src/lib/api.ts`

Use `docs/` selectively. Several docs are useful design history, but not all of them match the current runtime.

## Repo Shape

```text
app.py                     FastAPI app, route handlers, startup/shutdown, UI mounts
worker.py                  background queue worker that talks to ComfyUI
db.py                      SQLite schema and queue/preset/history operations
defs.py                    workflow YAML/template loader and validator
prompt_builder.py          prompt materialization, param coercion, resolution/orientation logic
comfy_client.py            ComfyUI HTTP client helpers used by worker/health checks
cli.py                     CLI for list/submit/status/cancel/retry
auto_prompt_cli.py         CLI for LM Studio-backed caption/motion prompt generation
auto_prompt/               auto-prompt generator, prompt text, caption cache
workflow_defs_v2/          active workflow definitions loaded by default
workflow_defs/             older workflow definitions still present in repo
static/index.html          legacy UI served at `/` and `/legacy`
ui/                        SvelteKit/Vite source for V2 UI served at `/v2` after build
ui/build/                  checked-in built V2 frontend assets
tests/                     Python tests plus a Node state test
docs/                      mixed implementation notes, specs, runbooks, and historical design docs
run.sh                     uvicorn launcher
requirements.txt           Python runtime deps
package.json               root Node package only used for jsdom-style tests
ui/package.json            actual V2 frontend toolchain
```

There is no `AI_GUIDE.md` in the current checkout yet.

## Runtime Entry Points

### Backend server

Primary start command:

```bash
./run.sh
```

That launches:

```bash
uvicorn app:app --host 0.0.0.0 --port "${PORT:-8585}"
```

The app startup sequence in `app.py`:

1. creates `data/`
2. loads workflow definitions from `WORKFLOW_DEFS_DIR`
3. initializes `QueueDB(data/queue.db)`
4. starts `Worker(...)`
5. mounts:
   - `/static` from `static/` when present
   - `/v2` from `ui/build` when present
   - `/` and `/legacy` to `static/index.html`

Important current-state detail: startup will fail if workflow templates cannot be loaded.

### Legacy UI

- `/` -> `static/index.html`
- `/legacy` -> alias to the same file

Treat this as still-live product surface, not a dead artifact. The backend explicitly keeps it mounted.

### V2 UI

- source: `ui/src/**`
- build output: `ui/build`
- served at `/v2`

If `ui/build` is missing, `app.py` returns HTTP 503 with setup instructions for `/v2`.

### CLI

Primary file: `cli.py`

Supported commands:

- `list`
- `submit --workflow ... --dir ... [--param key=value] [--dry-run]`
- `status [job_id]`
- `cancel <job_id>`
- `retry <job_id>`

Normal behavior is API-first against `VIDEO_QUEUE_API`. If the API is unreachable, `submit`, `status`, `cancel`, and `retry` fall back to local DB/workflow operations where implemented.

### Auto-prompt CLI

`auto_prompt_cli.py` is the direct entry point for LM Studio-backed prompt generation outside the web UI. It supports `caption`, `motion`, and `both` stages, plus a `--mock` mode for testing.

## Environment Model

The main runtime environment variables are:

- `VIDEO_QUEUE_ROOT`
  - app root for `data/`, `static/`, and default workflow dirs
  - default: `~/video_queue`
- `WORKFLOW_DEFS_DIR`
  - default: `$VIDEO_QUEUE_ROOT/workflow_defs_v2`
- `COMFY_ROOT`
  - default: `~/ComfyUI`
- `COMFY_BASE_URL`
  - default: `http://127.0.0.1:8188`
- `LMSTUDIO_URL`
  - default: `http://127.0.0.1:1234`
- `PORT`
  - backend port, default `8585`
- `VIDEO_QUEUE_API`
  - CLI target API base URL, default `http://127.0.0.1:8585`

Derived locations that matter operationally:

- DB: `data/queue.db`
- prompt logs: `data/logs/<job_id>_<prompt_row_id>.log`
- Comfy input root: `$COMFY_ROOT/input`
- staging area for submitted files: `$COMFY_ROOT/input/_video_queue_staging/<batch_token>/`

## Architecture

### 1. Workflow definition layer

`defs.py` is the schema boundary for workflow YAML.

Active defaults are loaded from `workflow_defs_v2/*.yaml`. Each workflow can use either:

- `template: <json file>`
- `template_inline: { ... }`

The loader validates:

- required fields like `name`, `description`, `input_type`, and `input_extensions`
- parameter metadata and type constraints
- file-binding references into template node IDs
- switch-state node references

This is the source of truth for what the UI and API expose.

### 2. Prompt-building layer

`prompt_builder.py` converts a workflow definition plus input files into one or more `PromptSpec` records.

It is responsible for:

- parameter coercion and unknown-param rejection
- applying file bindings for images/videos/input paths
- writing parameter overrides into prompt node inputs
- setting switch values
- randomizing seed values when configured
- writing output prefixes
- normalizing `WanContextWindowsManual` context schedule strings
- applying resolution presets
- swapping width/height when `flip_orientation` is requested
- repeating prompt generation based on `tries`

### 3. API submission layer

`app.py` exposes the queueing and UI API surface.

Notable endpoint groups:

- catalog and metadata:
  - `GET /api/workflows`
  - `GET /api/resolution-presets`
  - `GET /api/loras`
  - `GET /api/upscale-models`
- presets:
  - `GET/POST /api/prompt-presets`
  - `GET/POST /api/settings-presets`
- workflow reload:
  - `POST /api/reload/workflows`
  - `POST /api/reload/loras`
  - `POST /api/reload/upscale-models`
- auto prompt:
  - `GET /api/auto-prompt/capability`
  - `POST /api/auto-prompt`
- input helpers:
  - `POST /api/input-dirs/normalize`
  - `GET/POST /api/input-dirs/recent`
  - `GET /api/input-dirs/default`
  - `POST /api/pick-directory`
  - `POST /api/pick-image`
  - `POST /api/upload/input-image`
- queue and jobs:
  - `POST /api/jobs`
  - `POST /api/jobs/single`
  - `GET /api/jobs`
  - `GET /api/jobs/{job_id}`
  - `POST /api/jobs/{job_id}/cancel`
  - `POST /api/jobs/{job_id}/retry`
  - `POST /api/queue/pause`
  - `POST /api/queue/resume`
  - `POST /api/queue/clear`
  - `GET /api/health`
  - `GET /api/jobs/{job_id}/log`

Submission behavior that matters:

- input paths are normalized to tolerate WSL/Windows-style pasted paths
- image/video files are copied into a staging directory under `COMFY_ROOT/input`
- the DB stores original source paths in prompt rows, even though prompt JSON points at staged files
- `split_by_input` creates one job per source file
- `move_processed` is a request-level flag; it is not blindly inherited from every workflow

### 4. Queue persistence layer

`db.py` owns local persistence in SQLite.

Important tables:

- `jobs`
- `prompts`
- `queue_state`
- `input_dir_history`
- `prompt_presets`
- `settings_presets`

Operational semantics:

- queue pause/resume is stored in DB
- prompt rows are the real unit of execution
- job status is derived from prompt-row state
- cancel is cooperative:
  - pending prompts can be marked canceled
  - a currently running Comfy prompt is not actively canceled via ComfyUI API

### 5. Worker execution layer

`worker.py` is a background thread started by FastAPI startup.

It:

- polls `QueueDB.next_pending_prompt()`
- backs off when ComfyUI is unreachable
- sends prompt JSON to ComfyUI via `POST /prompt`
- polls Comfy history until completion
- stores output paths from history
- writes per-prompt logs under `data/logs/`
- reconciles interrupted/running prompts on restart
- moves source inputs into `<input_dir>/_processed` only when a job fully succeeds and `move_processed` is enabled

The worker does not copy or move generated Comfy outputs. It only records returned output paths.

## Frontend State

There are two frontend surfaces with different roles.

### Legacy static UI

`static/index.html` is still the root UI. If a task is about `/`, inspect that file directly.

### V2 Svelte UI

The V2 app in `ui/` is the active modernization path.

Current verified features from `ui/src/**` and tests:

- status bar with health, pause, resume, reload workflows, reload LoRAs
- workspace tabs with create/rename/close behavior
- submit panel with four tabs:
  - batch video generation
  - image generation
  - video upscale/interpolate
  - image upscale for I2V prep
- workflow-specific parameter forms driven from `/api/workflows`
- resolution preset picker
- drag/drop upload path for image inputs through `/api/upload/input-image`
- prompt preset and settings preset save/load
- per-image auto-prompt generation and application
- queue list with search, status filters, sorting, incremental rendering, multi-select, bulk actions

The V2 client talks directly to backend APIs in `ui/src/lib/api.ts`. There is no separate frontend-specific backend.

## Current Workflows

The active default workflow set in `workflow_defs_v2/` currently includes:

- `wan-context-2stage`
- `wan-context-2stage-split-prompts`
- `wan-context-3stage-split-prompts`
- `wan-context-lite-2stage`
- `image-gen-flux-img2img`
- `upscale-interpolate-only`
- `upscale-images-i2v`

Category mapping matters to the V2 UI:

- `video_gen`
- `image_gen`
- `video_upscale`
- `image_upscale`

Legacy workflows also still exist under `workflow_defs/`, but default app loading points at `workflow_defs_v2`.

## Testing Surface

Relevant test buckets:

- backend/API/db/worker tests in `tests/test_*.py`
- V2 UI contract checks in `tests/test_ui_v2_contract.py`, `tests/test_ui_v2_mount.py`, and related files
- Node-based frontend state test in `tests/frontend_state.test.mjs`
- end-to-end smoke helper in `test_e2e.py`

Root `package.json` is not the frontend app package. It only carries `jsdom` for Node-based tests. Use `ui/package.json` for actual frontend build/dev commands.

## Current State And Risks

Facts verified in this checkout:

- the repo name on disk is `video_queue`, but `README.md` still identifies the project as `comfy_frontend`
- there is no existing `AI_GUIDE.md`
- `ui/build` is checked in, so `/v2` can be served immediately if backend startup succeeds
- both legacy `/` and V2 `/v2` routes are intentionally active
- `workflow_defs_v2` is the live default, not `workflow_defs`

Important startup risk in this clone:

- five active `workflow_defs_v2/*.yaml` files use absolute `template:` paths under `/home/cobra/video_queue/...`
- those absolute paths do not exist in this checkout on 2026-03-21
- because `app.py` loads workflows during startup and `defs.py` requires template files to exist, server startup will fail until those template paths are corrected or the expected files exist at those absolute locations

When debugging runtime failures, check workflow definition paths first before investigating FastAPI, worker, or frontend code.

## Working Guidance

If you need to change behavior:

- API or queue semantics: start in `app.py`, `db.py`, and `worker.py`
- prompt parameter behavior: start in `prompt_builder.py` and the relevant YAML in `workflow_defs_v2/`
- workflow discovery/validation issues: start in `defs.py`
- LM Studio auto-prompt behavior: start in `auto_prompt/generator.py`
- V2 UX work: start in `ui/src/routes/+page.svelte`, `ui/src/lib/components/**`, and `ui/src/lib/stores/**`
- root-page UI work: inspect `static/index.html`

Do not assume older docs in `docs/` are current without checking code. The implementation in `app.py`, `worker.py`, `defs.py`, and `ui/src/**` is the source of truth.
