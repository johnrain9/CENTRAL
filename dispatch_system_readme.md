# Dispatcher System README

## Purpose

This is the operator and planner entrypoint for the autonomy dispatch system.

Current implementation lives in:

- repo: `/home/cobra/photo_auto_tagging`
- module: `/home/cobra/photo_auto_tagging/autonomy`

## Current Reality

The autonomy control plane exists and the CLI module works, but the environment is not fully polished yet:

- the intended command surface is `autonomy ...`
- on this machine, the installed console script is not currently available in the repo venv
- the working command today is:

```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
python -m autonomy.cli ...
```

- first-run requires profile bootstrap:

```bash
python -m autonomy.cli init --profile default
```

## Manual CLI Flow

Operator status and queue checks:

```bash
python -m autonomy.cli dispatch status --profile default
python -m autonomy.cli report summary --json --profile default
python -m autonomy.cli task eligible --json --profile default
```

Dispatch execution:

```bash
python -m autonomy.cli dispatch run-once --profile default
python -m autonomy.cli dispatch daemon --profile default
python -m autonomy.cli dispatch stop --profile default
```

Worker and report inspection:

```bash
python -m autonomy.cli worker list --json --profile default
python -m autonomy.cli report review-aging --json --profile default
python -m autonomy.cli report tail --profile default
```

Planner flow:

```bash
python -m autonomy.cli task list --json --status pending --profile default
python -m autonomy.cli task eligible --json --profile default
python -m autonomy.cli task blocked --json --profile default
python -m autonomy.cli graph list --json --profile default
```

## Shell Entry Point

The preferred shell command is `dispatcher`, provided by `~/.zshrc`.

It is backed by:

- script: `/home/cobra/CENTRAL/scripts/dispatcher_control.py`

Supported commands:

```bash
dispatcher
dispatcher start
dispatcher restart
dispatcher stop
dispatcher status
dispatcher logs
dispatcher follow
dispatcher once
```

Behavior:

- `dispatcher` defaults to `start`
- auto-runs `init --profile default` if needed
- launches `dispatch daemon` in the background
- writes launcher output to the profile state dir
- uses the autonomy lock file as the source of truth for running state

## On-Disk State

Default profile paths:

- profile root: `~/.autonomy/profiles/default`
- DB: `~/.autonomy/profiles/default/data/autonomy/autonomy.db`
- dispatcher lock: `~/.autonomy/profiles/default/.worker-state/dispatcher.lock`
- dispatcher log: `~/.autonomy/profiles/default/.worker-state/dispatcher.log`
- launcher log: `~/.autonomy/profiles/default/.worker-state/dispatcher-launcher.log`

## Codex Skills In Use

These are the relevant skills for dispatch-system support:

- `autonomy-operator`
  - run dispatcher, inspect workers, inspect queue pressure, tail logs
- `autonomy-planner`
  - create tasks, update tasks, inspect eligible/blocked graphs
- `autonomy-triage`
  - inspect failures, retries, stale reviews, approve/reject paths
- `multi-repo-planner`
  - keep the central board and cross-repo priorities aligned while the autonomy DB becomes the primary execution surface

## Current Gaps

1. Canonical `autonomy` console script is not reliably available from shell without repo-specific bootstrap.
2. Planner tasks still live primarily in repo-local `tasks.md` files instead of the autonomy DB.
3. Skills describe the canonical CLI, but local bootstrap/install reality still needs to be documented more explicitly.
4. Review/approval operating rhythm is not yet the default planner workflow.

## Planner Rule

The user should rarely have to create or update dispatch tasks manually.

Planner responsibility:

- add and update support tasks
- keep central tracking current
- convert planning intent into dispatchable tasks
- decide when a task belongs in central tracking vs the autonomy DB

User responsibility:

- mostly just start the dispatcher and ask for status
