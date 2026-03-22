# AI Guide: video_wall

This file is a draft for `/Users/paul/projects/video_wall/AI_GUIDE.md`.

It was validated against the cloned repo's implementation on 2026-03-21, but the current sandbox cannot write into that repo, so the guide is staged here as an artifact.

## Purpose

`video_wall` is a small local-first static gallery generator for browsing generated videos.

The repo is not a web app or package. The current product is one Python builder script that:

- scans configured folders for video files
- generates cached JPEG thumbnails and animated WEBP previews with `ffmpeg`
- probes duration with `ffprobe`
- writes a single self-contained `gallery.html` with inline CSS, inline JavaScript, and embedded item JSON

Serving is optional and static. The canonical runtime is `build_gallery.py` plus `python3 -m http.server`.

## Read First

Open files in this order:

1. `build_gallery.py`
2. `SERVING.md`
3. `CODEBASE_DATA_MODEL_AND_EXTENSIONS.md`
4. `docs/favorites_mode_design.md`
5. `HANDOFF_VIDEO_WALL.md`

Use `HANDOFF_VIDEO_WALL.md` as historical context only. It documents earlier transport and autoplay debugging. It is not the source of truth for the current UI.

## Repo Shape

Top-level files are the whole product surface:

- `build_gallery.py`
  - primary and current entry point
  - owns scan, preview generation, HTML generation, and all in-browser UI logic
- `gallery.html`
  - checked-in generated output from a previous build
  - treat as build artifact, not hand-authored source
- `SERVING.md`
  - canonical serving strategy
- `CODEBASE_DATA_MODEL_AND_EXTENSIONS.md`
  - implementation notes on current item schema and extension paths
- `HANDOFF_VIDEO_WALL.md`
  - historical debugging notes from an earlier iteration
- `generate_video_wall.py`
  - deprecated older generator that built a lazy-loaded grid of `<video>` tiles
- `serve_gallery.py`
  - deprecated helper wrapper around `ThreadingHTTPServer`
- `docs/favorites_mode_design.md`
  - design doc for favorites mode; much of it is now implemented in the current gallery
- `tasks.md`
  - lightweight task tracker for the repo

There is no Python package layout, no API server, and no automated test suite in this checkout.

## Current Architecture

The current implementation is static-site generation plus client-side state:

1. `build_gallery.py` recursively scans configured roots for `.mp4`, `.webm`, `.mov`, and `.mkv`.
2. For each file it derives a cache key from `path + size + mtime_ns`.
3. It creates:
   - one static JPEG thumbnail
   - one animated WEBP preview keyed by preview profile settings
4. It computes metadata for each item:
   - stable item ID
   - video URL
   - thumb URL
   - preview URL
   - label
   - source label
   - duration and formatted duration label
   - file size
   - mtime
5. It embeds the full item list into `gallery.html` as `const ITEMS = ...`.
6. The browser handles filtering, sorting, paging, favorites, modal playback, and root-list persistence entirely in local JavaScript.

Important: the grid is thumbnail-first now. It does not autoplay dozens of in-grid videos anymore. Full video playback happens only inside the modal when a card is opened.

## Runtime Entry Points

Primary command:

```bash
python3 build_gallery.py
```

Canonical serve flow from `SERVING.md`:

```bash
cd /home/cobra
python3 -m http.server 8888 --bind 0.0.0.0
```

Expected page URL in the documented WSL setup:

```text
http://localhost:8888/video_wall/gallery.html
```

Useful CLI options from `build_gallery.py --help`:

- `--roots ...`
- `--server-root`
- `--out`
- `--thumb-dir`
- `--thumb-width`
- `--thumb-time`
- `--preview-width`
- `--preview-time`
- `--preview-seconds`
- `--preview-fps`
- `--preview-quality`
- `--per-page`
- `--url-mode {http,file,wsl}`

## Defaults And Assumptions

Current defaults in `build_gallery.py`:

- roots:
  - `/home/cobra/output/amazing/best`
  - `/home/cobra/ComfyUI/output/upscaled_best`
  - `/home/cobra/ComfyUI/output/video/auto_batch`
- `server_root`: `/home/cobra`
- output HTML: `/home/cobra/video_wall/gallery.html`
- thumbnail cache dir: `/home/cobra/video_wall/thumbs`
- thumbnail width: `360`
- thumbnail timestamp: `00:00:00.50`
- preview width: `280`
- preview timestamp: `00:00:00.30`
- preview duration: `2.4`
- preview fps: `10`
- preview quality: `58`
- page size: `72`
- URL mode: `http`

External runtime dependencies are implicit, not packaged:

- Python 3
- `ffmpeg`
- `ffprobe`
- a static file server if using `http` mode

## UI Behavior

Everything below lives inside the generated HTML's inline script:

- search by filename substring
- filter by source root label
- sort by name, mtime, or size
- pagination
- modal player with play/pause, mute, and close
- favorites mode with `All` and `Favorites` header toggle
- favorite/unfavorite from both grid cards and modal
- keyboard shortcuts:
  - `f` toggles favorite on focused card or open modal item
  - `m` toggles mute for the modal player
  - `Escape` closes the modal
- local persistence in `localStorage`:
  - `video_wall:favorites:v1`
  - `video_wall:roots:v1`
- corruption handling for favorites storage:
  - invalid JSON is copied to a `:corrupt:<timestamp>` key and reset

The "Add Path" button does not rescan live. It updates a stored root list and helps the user copy a rebuild command. Rebuilding still requires rerunning `build_gallery.py`.

## Data Model

Per-item fields currently emitted into `ITEMS`:

- `id`
- `video`
- `thumb`
- `preview`
- `label`
- `source`
- `duration`
- `duration_label`
- `size`
- `mtime`

Stability notes:

- `id` is stable across rebuilds as long as the canonical path relative to `server_root` stays the same.
- thumbnail cache filenames are not stable IDs; they include mtime and file size.

Not present today:

- prompt metadata
- model / sampler / seed / workflow details
- ratings or shared persistence
- backend API
- image support

## Deprecated Paths

`generate_video_wall.py` and `serve_gallery.py` both print deprecation notices and should not be extended unless you are intentionally reviving old behavior.

- `generate_video_wall.py`
  - older generator
  - still creates a direct `<video>` wall
  - only supports `file` and `http` URL modes
- `serve_gallery.py`
  - tiny threaded HTTP server wrapper
  - replaced in practice by `python3 -m http.server`

If you need to change gallery behavior, start in `build_gallery.py` unless the task is specifically about historical compatibility.

## Current State

Facts verified in this checkout:

- there is no existing `AI_GUIDE.md`
- the repo has no `README.md`
- `gallery.html` is checked in
- favorites mode is already implemented in the generated gallery, not just designed
- the current gallery uses image previews in-grid and video playback in the modal
- serving docs still assume a WSL-oriented `/home/cobra` environment
- `HANDOFF_VIDEO_WALL.md` still describes an earlier troubleshooting phase and says the HTML uses in-grid video tiles; that is stale relative to `build_gallery.py`

## Change Guidance

When making changes:

- keep `build_gallery.py` as the source of truth and regenerate `gallery.html` if behavior changes
- validate whether a change belongs in build-time Python or runtime inline JavaScript before editing
- preserve backward-compatible CLI flags unless the task explicitly changes operator workflows
- be careful with path assumptions; many defaults are hard-coded to `/home/cobra`
- remember that root editing and favorites are browser-local unless you add a server-backed persistence layer

For common tasks:

- preview generation or cache behavior: `build_gallery.py` thumbnail helpers and main loop
- item schema changes: `build_gallery.py` item dict and inline `ITEMS` consumer code
- modal / filters / favorites behavior: inline script in `build_gallery.py`
- serving issues: `SERVING.md` first, then URL-building helpers in `build_gallery.py`
- future metadata or ratings work: `CODEBASE_DATA_MODEL_AND_EXTENSIONS.md` and `docs/favorites_mode_design.md`
