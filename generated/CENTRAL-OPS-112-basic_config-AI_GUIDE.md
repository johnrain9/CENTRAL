# AI Guide: basic_config

This file is a draft for `/Users/paul/projects/basic_config/AI_GUIDE.md`.

It was validated against the cloned repo's implementation on 2026-03-21, but the current sandbox cannot write into that repo, so the guide is staged here as an artifact.

## Purpose

`basic_config` is the workspace bootstrap repo for this machine layout.

It owns three related contracts:

- tracked shared dotfiles for zsh and git
- one shared shell utility at `bin/.local/bin/ffmpeg_wrap.sh`
- the manifest-driven checkout flow that populates `PROJECTS_DIR` and gives shell helpers stable sibling-repo locations

The repo is intentionally small. The important split is:

- `setup.sh` manages repo checkout from `projects.manifest.tsv`
- `bootstrap.sh` links tracked files into `$HOME`
- `zsh/` defines shell startup, repo discovery, and workstation helper commands after those links are active

Do not assume `setup.sh` changes live dotfiles by default. Dotfile activation is explicit.

## Read First

Open these files first:

1. `README.md`
2. `setup.sh`
3. `bootstrap.sh`
4. `projects.manifest.tsv`
5. the relevant zsh fragment or tracked config file for your task

Then branch by concern:

- checkout/bootstrap flow: `setup.sh`
- symlink and backup behavior: `bootstrap.sh`
- shell startup and environment defaults: `zsh/.zshrc` and `zsh/.zshrc.d/00-base.zsh`
- aliases and workstation helpers: `zsh/.zshrc.d/10-aliases.zsh` and `zsh/.zshrc.d/30-tooling.zsh`
- platform-specific path behavior: `zsh/.zshrc.d/50-macos.zsh` and `zsh/.zshrc.d/50-wsl.zsh`
- default shell landing directory: `zsh/.zshrc.d/90-projects-default.zsh`
- shared git defaults: `git/.gitconfig`
- ffmpeg utility behavior: `bin/.local/bin/ffmpeg_wrap.sh`

## Repo Shape

```text
README.md
setup.sh                   workspace bootstrap entry point
bootstrap.sh               dotfile symlink manager
projects.manifest.tsv      source of truth for sibling repo checkouts
zsh/
  .zshrc                   thin entrypoint that sources tracked fragments
  .zshrc.d/                shared shell fragments loaded in lexical order
  .zshrc.local.example     copied to ~/.zshrc.local if missing
git/
  .gitconfig               tracked git defaults plus local include
  .gitconfig.local.example copied to ~/.gitconfig.local if missing
bin/.local/bin/
  ffmpeg_wrap.sh           small ffmpeg helper utility
```

There is no `AI_GUIDE.md` in the current checkout yet.

## Entry Points

### `setup.sh`

`setup.sh` is the top-level bootstrap command.

Observed behavior from the script:

- reads `projects.manifest.tsv`
- uses `PROJECTS_DIR`, defaulting to `$HOME/projects`
- creates the projects directory if needed
- clones missing repos from the manifest
- prints `present` for existing git repos
- optionally runs `git pull --ff-only` in existing repos with `--pull-existing`
- prints `skip` for existing paths that are not git repos
- supports repeatable `--repo` filtering
- supports `list`, `status`, `link-dotfiles`, and `--link-dotfiles`
- supports `--dry-run` without cloning or linking

Important command distinction:

- `./setup.sh link-dotfiles`
  runs `bootstrap.sh` immediately and exits
- `./setup.sh --link-dotfiles`
  performs checkout work first, then runs `bootstrap.sh`

`status` reports both manifest repo presence and current link state for:

- `~/.zshrc`
- `~/.zshrc.d`
- `~/.gitconfig`
- `~/.local/bin/ffmpeg_wrap.sh`

### `bootstrap.sh`

`bootstrap.sh` only manages the tracked dotfile links and local example-file creation.

It does all of the following:

- creates `~/projects`
- creates `~/.local/bin`
- links `zsh/.zshrc` to `~/.zshrc`
- links `zsh/.zshrc.d` to `~/.zshrc.d`
- links `git/.gitconfig` to `~/.gitconfig`
- links `bin/.local/bin/ffmpeg_wrap.sh` to `~/.local/bin/ffmpeg_wrap.sh`
- copies `zsh/.zshrc.local.example` to `~/.zshrc.local` if missing
- copies `git/.gitconfig.local.example` to `~/.gitconfig.local` if missing

If a target exists and is not already the expected symlink, the script moves it under:

`~/.dotfiles-backups/<timestamp>/...`

That timestamped backup path is the repo's main safety mechanism.

## Manifest Model

`projects.manifest.tsv` is the source of truth for which repos should exist under the workspace checkout root.

Format:

```text
repo_name<TAB>git_remote_url
```

Comment lines starting with `#` are ignored.

Current manifest entries are:

- `CENTRAL`
- `aimSoloAnalysis`
- `basic_config`
- `motoHelper`
- `photo_auto_tagging`
- `unified_video_app`
- `video_queue`
- `video_wall`
- `voice_transcription`
- `Dispatcher`
- `claudeConfig`

Non-obvious current-state detail: some remotes still use older or different GitHub repo names, including:

- `photo_auto_tagging` -> `johnrain9/photo_tagger`
- `video_queue` -> `johnrain9/comfy_frontend`

Treat the manifest file, not repo-name intuition, as canonical.

## Zsh Loading Model

The tracked `zsh/.zshrc` is deliberately minimal. It:

1. sources every readable `~/.zshrc.d/*.zsh` fragment in lexical order
2. sources `~/.zshrc.local` last if present

This means shared behavior belongs in tracked fragments, while machine-only overrides belong in the local file.

### Shared base behavior

`zsh/.zshrc.d/00-base.zsh` is the most important shell file. It sets:

- history configuration and sizes
- `PROJECTS_DIR="${PROJECTS_DIR:-$HOME/projects}"`
- platform detection via `MY_PLATFORM` with values `macos`, `wsl`, `linux`, or `unknown`
- PATH prepending for `~/.local/bin`
- `dot_repo_dir` for sibling-repo lookup
- `dot_open_url` for cross-platform URL opening
- `dot_primary_ip` for basic local IP discovery
- zsh completion via `compinit`
- a prompt that shows the current git branch when available
- history-search key bindings for arrows and `Ctrl-P` / `Ctrl-N`

`dot_repo_dir` resolves repositories in this order:

1. `$PROJECTS_DIR/<repo>`
2. `$HOME/<repo>`

That fallback is intentional. Several helpers still tolerate an older `$HOME/<repo>` layout.

### Other fragments

- `10-aliases.zsh` defines small aliases plus `aa` and `webui` helpers for `stable-diffusion-webui`
- `15-nvm.zsh` loads nvm if present
- `50-macos.zsh` prepends common Homebrew paths on macOS
- `50-wsl.zsh` is currently a guarded placeholder for tracked WSL-wide settings
- `90-projects-default.zsh` changes directory to `PROJECTS_DIR` only for interactive shells that start in `$HOME`

That last rule is subtle but important: the shell's starting directory can change to `~/projects`, but `$HOME` itself is not redefined.

## Tooling Helpers

`zsh/.zshrc.d/30-tooling.zsh` contains most workstation-specific helpers. They are convenience wrappers around sibling repos, not portable project APIs.

Current helpers include:

- `comfy`: start, restart, stop, status, and logs for a local `ComfyUI` process
- `aim`: wrapper around `CENTRAL/scripts/aim_control.py`
- `dispatcher`: wrapper around `CENTRAL/scripts/dispatcher_control.py`
- `gallery` and `gallery_http`: rebuild and serve `video_wall/gallery.html`
- `gallery_file`: WSL-only gallery build that opens the generated file via `wsl.localhost`
- `comfy-run`, `queue`, `queue_restart`, and queue aliases: wrappers around the `video_queue` repo
- `queue_v2`: start/stop the queue UI or invoke `video_queue/cli.py`
- `vapp` and `vapp-open`: helpers for `unified_video_app`
- `genflow`: wrapper around `photo_auto_tagging`'s `genflow` CLI and UI entrypoint
- `pq`: start/stop the PhotoQuery UI or invoke the `pq` CLI in `photo_auto_tagging`
- legacy convenience wrappers like `i2v_legacy`, `i2v_single_legacy`, and `upscale`

If a helper looks broken, verify the expected sibling repo name and virtualenv path before changing the function body.

## Git Config

`git/.gitconfig` is intentionally narrow. It currently sets:

- `autocrlf = input`
- `eol = lf`
- GitHub and Gist credential helpers via `gh auth git-credential`
- a tracked `[user]` name and email
- inclusion of `~/.gitconfig.local`

The tracked user identity is an implementation fact in this repo, not just a README suggestion. Treat edits there as user-visible.

## Shared Utility Script

`bin/.local/bin/ffmpeg_wrap.sh` exposes two supported subcommands:

- `last-frame`: extract the final frame from a video
- `stitch`: concatenate two clips, trying stream copy first and falling back to H.264/AAC re-encode

Implementation details worth knowing:

- it requires both `ffmpeg` and `ffprobe`
- `last-frame` first tries a frame-count-based exact selection, then falls back to `-sseof`, then to `-vf reverse`
- `stitch` builds a temporary concat list with absolute paths from `realpath`

If you change this file, validate both the fast path and the fallback path.

## Working Rules For AI Agents

- Start with `README.md`, but trust the scripts over the README if they diverge.
- Hidden files matter in this repo. Use file listings that include dotfiles.
- Do not assume `setup.sh` mutates live dotfiles by default. It does not.
- Treat `projects.manifest.tsv` as the canonical workspace inventory.
- Treat `bootstrap.sh` as the canonical symlink contract into `$HOME`.
- Treat shell helpers as workstation conveniences that depend on the surrounding sibling repos already existing.
- Keep cross-machine behavior in tracked files and secrets or machine-only tweaks in the example-derived local files.

## Validation Notes

This guide was checked against:

- `README.md`
- `setup.sh`
- `bootstrap.sh`
- `projects.manifest.tsv`
- `zsh/.zshrc`
- every file under `zsh/.zshrc.d/`
- `zsh/.zshrc.local.example`
- `git/.gitconfig`
- `git/.gitconfig.local.example`
- `bin/.local/bin/ffmpeg_wrap.sh`
- `setup.sh --help`
- `setup.sh list`
- `setup.sh --dry-run --repo basic_config --repo CENTRAL --link-dotfiles`

Validation was by direct source inspection plus safe read-only command checks. `bootstrap.sh` was not executed because it would modify files under `$HOME`.
