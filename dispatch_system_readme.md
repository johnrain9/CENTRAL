# Dispatcher System README

## Purpose

This is the operator and planner entrypoint for the autonomy dispatch system.

Current implementation lives in:

- repo: `/home/cobra/photo_auto_tagging`
- module: `/home/cobra/photo_auto_tagging/autonomy`

## Runtime Contract

Preferred daily entrypoints:

- `dispatcher ...` for start/stop/status/log control from any shell with `~/.zshrc` loaded
- `autonomy ...` inside the activated `/home/cobra/photo_auto_tagging/.venv`
- `python -m autonomy.cli ...` only as a fallback if the console script is missing

Bootstrap the runtime with:

```bash
cd /home/cobra/photo_auto_tagging
source .venv/bin/activate
if ! command -v autonomy >/dev/null 2>&1; then
  ./.venv/bin/python -m pip install -e .
fi
autonomy init --profile default
```

Notes:

- `pyproject.toml` already defines the `autonomy` and `autonomy-cli` console scripts.
- The supported fix for a missing `autonomy` binary is an editable install into the repo venv.
- `dispatcher` prefers `.venv/bin/autonomy` and falls back to `python -m autonomy.cli` if the console script is not present yet.
- First-run profile bootstrap remains required for each profile.

## Manual CLI Flow

Operator status and queue checks:

```bash
autonomy dispatch status --profile default
autonomy report summary --json --profile default
autonomy task eligible --json --profile default
```

Dispatch execution:

```bash
autonomy dispatch run-once --profile default
autonomy dispatch daemon --profile default
autonomy dispatch stop --profile default
```

Worker and report inspection:

```bash
autonomy worker list --json --profile default
autonomy report review-aging --json --profile default
autonomy report tail --profile default
```

Planner flow:

```bash
autonomy task list --json --status pending --profile default
autonomy task eligible --json --profile default
autonomy task blocked --json --profile default
autonomy graph list --json --profile default
```

Fallback if the console script is not installed yet:

```bash
python -m autonomy.cli dispatch status --profile default
python -m autonomy.cli task eligible --json --profile default
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
- prefers the `autonomy` console script when it exists in the repo venv
- falls back to `python -m autonomy.cli` without changing `dispatcher` usage
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
  - keep the central board and cross-repo priorities aligned while markdown remains a mirror during migration

## Canonical Docs

Canonical autonomy operator/planner/triage docs now live in `CENTRAL`:

- [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
- [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
- [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
- [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)

`/home/cobra/photo_auto_tagging/docs/autonomy_skills/` is now implementation-local and should only keep stubs or code-adjacent notes.

## Planner-Owned Ingestion Workflow

The planner, not the user, owns turning board items into autonomy tasks.

Working sequence:

1. Capture intent from repo-local `tasks.md` or `CENTRAL/tasks.md`.
2. Create or update the autonomy DB task with explicit repo root, prompt body, and validation notes.
3. Set dependency edges before promotion.
4. Promote the task to `pending` only when it is runnable without more user clarification.
5. After worker completion or review outcome, mirror the final state back to markdown tracking.

Canonical commands:

```bash
autonomy task create "Title" --category implementation --repo-root "/abs/repo" --prompt-body "..." --status draft --profile default
autonomy task update T000123 --prompt-body "..." --profile default
autonomy task set-dependencies T000123 --dependency "T000100,T000101" --profile default
autonomy task start T000123 --profile default
autonomy task eligible --json --profile default
autonomy task blocked --json --profile default
```

Ownership rules:

- Planner owns task creation, prompt refinement, dependency maintenance, and promotion to `pending`.
- Worker owns implementation plus closeout evidence: tests run, commit/ref, and blocker statement if blocked.
- Planner owns markdown mirror updates in repo boards and [`tasks.md`](/home/cobra/CENTRAL/tasks.md) after autonomy state changes.
- User should only need to request work or ask for status; the planner performs the bookkeeping.

Source roles during the transition:

- Repo-local markdown boards: backlog intake and human-readable repo roadmap
- autonomy DB: dispatchable execution state, dependencies, retries, approvals
- `CENTRAL/tasks.md`: cross-repo mirror and portfolio summary

## Planner Rule

The user should rarely have to create or update dispatch tasks manually.

Planner responsibility:

- add and update support tasks
- keep central tracking current
- convert planning intent into dispatchable tasks
- decide when a task belongs in central tracking vs the autonomy DB

User responsibility:

- mostly just start the dispatcher and ask for status

## Review And Retry Runbook

Check these surfaces each dispatch cycle or at least daily:

```bash
autonomy report review-aging --json --profile default
autonomy report failures --json --profile default
autonomy worker list --json --profile default
```

Per-task inspection:

```bash
autonomy task show T000123 --json --profile default
autonomy worker inspect T000123 --json --profile default
autonomy worker tail T000123 --profile default
```

Decision rules:

- Approve with `autonomy task approve ...` when acceptance is met and the closeout includes concrete evidence.
- Reject with `autonomy task reject ... --notes "..."` when scope, correctness, or evidence is insufficient and a human-readable reason is needed for replanning.
- Reset with `autonomy task reset ...` for transient infra/runtime failures before a fresh dispatch attempt.
- Retry with `autonomy worker retry ...` only when the prior run produced enough evidence to justify another execution without rewriting the task.
- Leave blocked when upstream dependencies or missing external inputs still prevent useful progress.

Required closeout evidence:

- commands/tests run and result summary
- commit hash, branch, or file reference
- concise blocker statement when not done

Stale-review clearing rhythm:

1. Inspect `review-aging` output.
2. Open the task/run evidence.
3. Approve, reject, or reset in the same session.
4. Mirror the decision back to central tracking if it changes portfolio state.

## Source-Of-Truth Migration

Phase 0: bootstrap

- Markdown boards remain authoritative for backlog discovery.
- autonomy DB is authoritative only for tasks already created there.
- Planner must mirror important state changes both ways.

Phase 1: planner-owned execution

- New dispatchable work is created in autonomy first.
- Repo boards remain a summarized mirror for humans and repo-specific planning notes.
- `CENTRAL/tasks.md` mirrors cross-repo status, not low-level dependency state.

Phase 2: autonomy-primary

- autonomy DB becomes the system of record for active and queued execution work.
- Markdown boards keep only high-level milestones, imported backlog summaries, or archived snapshots.

Drift resolution:

- If autonomy and markdown disagree, follow the authoritative source for the current phase.
- Planner fixes the non-authoritative surface in the same work session that discovers drift.
- Do not resolve drift by editing SQLite directly.

Rollback:

- Stop creating new DB-only tasks.
- Reassert repo markdown boards and `CENTRAL/tasks.md` as the planning source.
- Export active autonomy tasks into markdown until tooling gaps are addressed.
