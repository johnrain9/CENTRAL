# AI Guide: unified_video_app

This file is a draft for `/Users/paul/projects/unified_video_app/AI_GUIDE.md`.

It was validated against the cloned repo's implementation on 2026-03-21, but the current sandbox cannot write into that repo, so the guide was staged here as an artifact.

## Purpose

`unified_video_app` is a single-repo replacement for separate queue and gallery tools. The backend is a FastAPI app with a SQLite runtime database and three long-running background loops. The frontend is a small React/Vite SPA that talks only to the backend's REST and SSE endpoints.

Use this guide to orient quickly before changing code.

## Read First

Open these files first:

1. `README.md`
2. `RUNBOOK.md`
3. `backend/main.py`
4. `backend/core/config.py`
5. `backend/models/queue.py`
6. `backend/models/media.py`
7. One router or service file relevant to your task

If the task is frontend-only, read:

1. `frontend/src/App.tsx`
2. `frontend/src/lib/api.ts`
3. The page component you are changing

## Current Architecture

### Backend entry point

- `backend/main.py` creates the FastAPI app and registers routers.
- The FastAPI lifespan does real startup work:
  - creates runtime directories
  - runs Alembic migrations to `head`
  - seeds `queue_state` and default `app_settings`
  - starts `WorkerService`, `ScannerService`, and `PreviewBuilderService`
- Shutdown stops those three services cleanly.

### Frontend entry point

- `frontend/src/main.tsx` boots the React app.
- `frontend/src/App.tsx` defines routes:
  - `/queue`
  - `/gallery`
  - `/jobs/:jobId`
- `/` redirects to `/queue`.

### Runtime model

`backend/core/config.py` reads all runtime settings from environment variables once at import time through the global `SETTINGS` object.

Important defaults:

- `VIDEO_HUB_ROOT`: `~/video_hub`
- `VIDEO_HUB_DB_PATH`: defaults to `<VIDEO_HUB_ROOT>/video_hub.db`
- `VIDEO_HUB_PORT`: `8080`
- `VIDEO_HUB_FRONTEND_PORT`: only used by `run.sh`, default `5173`
- `COMFY_BASE_URL`: `http://127.0.0.1:8188`
- `VIDEO_SCAN_ROOTS`: defaults to `<VIDEO_HUB_ROOT>/outputs`
- `SCAN_INTERVAL_SECONDS`: `20`
- `PREVIEW_CONCURRENCY`: `2`

Derived directories:

- logs: `<VIDEO_HUB_ROOT>/logs`
- previews: `<VIDEO_HUB_ROOT>/previews`
- default Comfy output dir: `<VIDEO_HUB_ROOT>/outputs`

## High-Signal Repo Map

- `backend/main.py`: app assembly and service startup
- `backend/core/config.py`: environment-driven settings
- `backend/core/defs.py`: workflow loading and parameter coercion
- `backend/core/prompt_builder.py`: turns workflow templates into prompts
- `backend/core/path_security.py`: allowlist-based path validation
- `backend/core/events.py`: in-process event bus and SSE formatting
- `backend/models/db.py`: SQLite connection setup and Alembic invocation
- `backend/migrations/versions/0001_initial.py`: current schema baseline
- `backend/models/queue.py`: job/prompt lifecycle and queue state
- `backend/models/media.py`: media catalog, preview rows, scan bookkeeping
- `backend/services/comfy_client.py`: ComfyUI transport and history polling
- `backend/services/worker.py`: serial prompt execution loop
- `backend/services/ingest.py`: converts prompt outputs into catalog rows
- `backend/services/scanner.py`: periodic filesystem scan loop
- `backend/services/preview_builder.py`: ffmpeg/ffprobe preview generation
- `backend/routers/`: HTTP API surface
- `backend/workflow_defs/*.yaml`: workflow definitions shown in UI and used by CLI/API
- `backend/cli.py`: operational CLI with API-first, DB-fallback behavior
- `frontend/src/pages/Queue.tsx`: queue form and queue/job controls
- `frontend/src/pages/Gallery.tsx`: gallery search/filter/pagination/modal playback
- `frontend/src/pages/JobDetail.tsx`: per-job drilldown and gallery deep-link
- `tests/`: behavior-level coverage for queue semantics, lifecycle, security/scanning, and SSE

## API Surface

Routers are mounted directly on the FastAPI app.

Implemented endpoints:

- `GET /`
- `GET /api/health`
- `GET /api/events/stream`
- `GET /api/workflows`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/jobs/{job_id}/retry`
- `GET /api/jobs/{job_id}/log`
- `GET /api/queue/state`
- `POST /api/queue/pause`
- `POST /api/queue/resume`
- `GET /api/media`
- `GET /api/media/{media_id}`
- `POST /api/media/rebuild-previews`
- `GET /media/file/{media_id}`
- `GET /previews/{media_id}`
- `GET /thumbs/{media_id}`

The frontend fetch layer in `frontend/src/lib/api.ts` currently uses only part of that surface. It does not yet wrap `GET /api/media/{id}` or `POST /api/media/rebuild-previews`.

SSE topics emitted by the backend event bus:

- `job.updated`
- `prompt.updated`
- `media.added`
- `media.updated`
- `scan.updated`

## Queue and Job Semantics

Queue behavior lives mainly in `backend/models/queue.py` and `backend/services/worker.py`.

Important rules:

- Jobs contain one or more prompts.
- Prompt count comes from workflow param `tries`; `backend/core/prompt_builder.py` expands one logical job into N prompts and increments `seed` per try.
- Prompt selection order is `job.priority DESC`, then `prompt.created_at ASC`, then `prompt.id ASC`.
- The worker processes only one prompt at a time.
- Queue pause only blocks selection of new pending prompts.
- `cancel_job()` changes only `pending` prompts to `canceled`.
- A currently `running` prompt is not force-killed by cancel.
- `retry_job()` requeues only prompts already in `failed`.
- On service startup, `recover_interrupted()` marks any leftover `running` prompts as `failed` with `error_message='interrupted'`.
- Comfy unavailability triggers a backoff ladder of `5, 10, 30, 60` seconds.

Observed status model:

- Job statuses used in code: `pending`, `running`, `paused`, `succeeded`, `failed`, `canceled`
- Prompt terminal statuses: `succeeded`, `failed`, `canceled`

Note: `paused` is part of `JOB_ACTIVE_STATUSES`, but current queue mutations do not set any job row to `paused`; pause state lives in `queue_state.is_paused`.

## Media, Scanning, and Previews

Media behavior spans `backend/models/media.py`, `backend/services/ingest.py`, `backend/services/scanner.py`, and `backend/services/preview_builder.py`.

### Ingest path

- After a prompt succeeds, `WorkerService` calls `ingest_prompt_outputs()`.
- Relative output paths are resolved under `SETTINGS.comfy_output_dir`.
- Existing files are upserted into `media_items` as `origin_type='queue_output'`.
- ffprobe is used to populate `duration_seconds` when possible.
- A preview row for the active preview profile is queued as `pending`.
- `media.added` SSE events are emitted per ingested file.

### Scanner path

- `ScannerService` loops across `SETTINGS.scan_roots`.
- Allowed scanned extensions are `.mp4`, `.webm`, `.mov`, `.mkv`.
- Discovered files are upserted as `origin_type='filesystem_scan'`.
- Preview rows are queued as `pending`.
- Scan summary is persisted in `media_scan_state`.
- A `scan.updated` event is emitted after each root scan.

### Preview builder

- `PreviewBuilderService` polls for `media_previews.status='pending'`.
- It marks rows `generating`, then shells out to `ffmpeg` twice:
  - animated `.webp` preview
  - `.jpg` thumb
- Success sets the row to `done`.
- Failure sets the row to `failed` and records `last_error`.
- `POST /api/media/rebuild-previews` only requeues rows already in `failed` or `pending`.

## Security Model

Path safety is centralized in `backend/core/path_security.py` and enforced by API routes.

Key behavior validated by code/tests:

- traversal attempts are rejected
- out-of-allowlist access is rejected
- job creation validates `input_dir` against `[SETTINGS.root, *SETTINGS.scan_roots, Path.home(), Path.cwd(), /tmp]`
- media file serving is restricted to `SETTINGS.root` and `SETTINGS.scan_roots`
- preview/thumb serving is restricted to `SETTINGS.previews_dir`
- `/media/file/{media_id}` supports HTTP range requests for browser playback

## Workflow Definitions

Workflow definitions are YAML files in `backend/workflow_defs/`.

Current workflows:

- `simple_image`
- `simple_video`

`backend/core/defs.py` loads them dynamically and validates/coerces params by type (`int`, `float`, `bool`, `str`). Unknown parameters are rejected.

The queue form on `/queue` is built dynamically from these definitions.

## Frontend Behavior

### Queue page

`frontend/src/pages/Queue.tsx`:

- fetches workflows, jobs, and queue state
- builds the submission form from workflow metadata
- supports pause, resume, cancel, retry, details, and merged job log
- refreshes on all SSE events, without topic filtering

Implementation note: the page uses both the typed API helper and one direct `fetch('/api/jobs/:id')` call for the Details button.

### Gallery page

`frontend/src/pages/Gallery.tsx`:

- syncs filters to URL search params
- supports `query`, `source`, `sort`, `page`, and optional `job_id`
- fetches 72 items per page
- refreshes only on `media.added` and `media.updated` SSE events
- renders previews with an `<img>` fallback chain:
  - `/previews/{id}`
  - `/thumbs/{id}`
  - inline "Preview unavailable" state
- opens modal playback using `<video src="/media/file/{id}">`
- keyboard shortcuts:
  - `Escape` closes the modal
  - `m` opens the first visible item

### Job detail page

`frontend/src/pages/JobDetail.tsx`:

- loads one job by route param
- shows job status and prompt rows
- links to `/gallery?job_id=<id>`

## CLI

`backend/cli.py` supports:

- `list`
- `submit`
- `status`
- `cancel`
- `retry`

Behavior:

- startup creates runtime dirs, runs migrations, and ensures queue state
- when the API server is reachable, commands use HTTP
- when the API server is unreachable, submit/list/status/cancel/retry fall back to direct DB logic
- `submit --dry-run` prints generated prompts without creating a job

## Tests and What They Prove

- `tests/test_queue_semantics.py`
  - interrupted running prompts become failed
  - cancel affects only pending prompts
  - retry does not requeue canceled prompts
- `tests/test_security_and_scanner.py`
  - path traversal and out-of-root access are rejected
  - scanner discovers new video files
- `tests/test_sse.py`
  - SSE endpoint uses `text/event-stream`
  - event bus delivers emitted lifecycle events
- `tests/test_lifecycle.py`
  - end-to-end submit -> worker -> ingest -> gallery visibility flow works when `ffmpeg` and `ffprobe` are installed

Test fixtures in `tests/conftest.py` mutate the global `SETTINGS` object to point at a temp runtime root and run migrations for each test.

## Local Dev Commands

From repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

Frontend/backend URLs during local dev:

- frontend: `http://127.0.0.1:5173`
- backend: `http://127.0.0.1:8080`

Useful test commands:

```bash
pytest
pytest tests/test_queue_semantics.py
pytest tests/test_security_and_scanner.py
pytest tests/test_sse.py
pytest tests/test_lifecycle.py
```

## Current State and Gaps

These are current implementation truths worth knowing before editing:

- The repo already contains backend, frontend, migrations, and tests; it is not a stub.
- The frontend is intentionally small and does not use a global state library.
- SSE is in-process only; there is no external broker.
- Preview generation depends on local `ffmpeg`/`ffprobe`.
- `run.sh` auto-installs frontend dependencies if `frontend/node_modules` is missing.
- There is no `AI_GUIDE.md` in the cloned repo yet.
- Some README language implies broader coverage than the tests actually enforce; rely on code and tests first when updating docs.

## Common Edit Paths

If you need to:

- change startup or service wiring: edit `backend/main.py`
- change queue semantics: edit `backend/models/queue.py` and `backend/services/worker.py`
- change media catalog behavior: edit `backend/models/media.py` and related services
- change API behavior: edit the matching file in `backend/routers/`
- add a workflow parameter: edit the YAML file in `backend/workflow_defs/` and verify frontend form behavior
- change frontend polling/SSE behavior: edit `frontend/src/lib/api.ts`, `frontend/src/hooks/useSSE.ts`, and the relevant page

## Documentation Rule

When updating docs in this repo, verify claims against implementation files and tests. Do not describe features that exist only in `tasks.md` or in the historical baseline repos.
