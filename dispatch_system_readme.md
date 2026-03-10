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
  - keep canonical CENTRAL tasks, repo targeting, and cross-repo priorities aligned during migration

## Canonical Docs

Canonical autonomy operator/planner/triage docs now live in `CENTRAL`:

- [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
- [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
- [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
- [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)

`/home/cobra/photo_auto_tagging/docs/autonomy_skills/` is now implementation-local and should only keep stubs or code-adjacent notes.

## Planner-Owned Ingestion Workflow

The planner, not the user, owns turning canonical CENTRAL tasks into autonomy tasks.

Working sequence:

1. Author or update the canonical task in `CENTRAL/tasks/<TASK_ID>.md`.
2. Read `Target Repo`, acceptance, and testing directly from the canonical CENTRAL task.
3. Create or update the autonomy DB task with explicit repo root, prompt body, and validation notes derived from that task.
4. Set dependency edges before promotion.
5. Promote the task to `pending` only when it is runnable without more user clarification.
6. After worker completion or review outcome, update the canonical CENTRAL task first, then any summary or repo-local mirror.

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
- Planner owns updates to canonical CENTRAL task files, then summary/mirror updates in [`tasks.md`](/home/cobra/CENTRAL/tasks.md) and any repo-local boards after autonomy state changes.
- User should only need to request work or ask for status; the planner performs the bookkeeping.

Source roles during the transition:

- `CENTRAL/tasks/<TASK_ID>.md`: canonical planner-owned task definition and closeout record
- autonomy DB: dispatchable execution state, dependencies, retries, approvals
- `CENTRAL/tasks.md`: summary index and portfolio view
- repo-local markdown boards: optional mirrors, local intake, or repo-specific roadmap context

## Planner Rule

The user should rarely have to create or update dispatch tasks manually.

Planner responsibility:

- add and update support tasks
- keep canonical CENTRAL task records current
- convert planning intent into dispatchable tasks
- decide when a task belongs in CENTRAL canonical tracking vs the autonomy DB

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

Phase 0: canonical authoring bootstrap

- Planner-owned tasks are authored in `CENTRAL/tasks/`.
- autonomy DB is authoritative for runtime execution state after a canonical task is ingested there.
- Repo boards may still provide intake and mirror context where migration is incomplete.

Phase 1: planner-owned execution

- New dispatchable planner-owned work starts from a canonical CENTRAL task file.
- autonomy DB mirrors that task into runnable state and review workflow.
- Repo boards remain summarized mirrors for humans and repo-specific notes.

Phase 2: CENTRAL-authored, autonomy-executed steady state

- `CENTRAL/tasks/` remains the authored source of truth for planner-owned work.
- autonomy DB remains the execution-state system of record.
- Repo boards keep only local roadmap notes, optional mirrors, or archived snapshots.

Drift resolution:

- If autonomy and markdown disagree, keep authored task content in `CENTRAL/tasks/` and runtime state in autonomy aligned according to the current phase.
- Planner fixes the non-authoritative surface in the same work session that discovers drift.
- Do not resolve drift by editing SQLite directly.

Rollback:

- Stop creating new DB-only tasks.
- Continue authoring planner-owned tasks in `CENTRAL/tasks/`.
- Export active autonomy state back into CENTRAL canonical tasks and summary records until tooling gaps are addressed.
