# autonomy-operator

Purpose:

- Keep dispatcher and worker fleet healthy during daily operation.
- Provide a deterministic operating rhythm for claim, run, recover, and backlog review.
- Surface stale reviews before they block throughput.
- Treat `autonomy` as a legacy execution surface where it still exists; CENTRAL DB is the primary control plane for canonical task state.

Bootstrap contract:

- Primary shell entrypoint: `dispatcher ...`
- Concurrency control: `dispatcher start --max-workers <n>` for an immediate override, or `dispatcher config --max-workers <n>` to persist the launcher default
- Primary CLI surface inside `/home/cobra/photo_auto_tagging/.venv`: `autonomy ...`
- If `autonomy` is missing after activation, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Start, stop, or pulse the dispatcher.
- Inspect active runs with the canonical worker-status tool before tailing logs.
- Surface completion, failure, and review-aging pressure.
- Hand off approval and rejection decisions to explicit task commands.
- When task context is needed for planner-owned work, inspect CENTRAL DB-backed task state first and treat markdown task cards as non-canonical exports only.

Command dependencies:

- `autonomy dispatch status --profile default`
- `autonomy dispatch run-once --profile default`
- `autonomy dispatch daemon --profile default`
- `autonomy dispatch stop --profile default`
- `autonomy dispatch tail --profile default`
- `autonomy worker list --json --profile default`
- `autonomy worker inspect <id> --json --profile default`
- `autonomy worker tail <id> --profile default`
- `autonomy worker terminate <id> --profile default`
- `autonomy report summary --json --profile default`
- `autonomy report stale --json --profile default`
- `autonomy report review-aging --json --profile default`
- `autonomy report tail --profile default`

Daily rhythm:

1. Check dispatcher state with `dispatcher status`.
2. Inspect active runs with `dispatcher workers --json` to confirm heartbeat freshness, log recency, and stuck-suspect heuristics.
3. Confirm worker concurrency from `configured_max_workers` / `next_start_max_workers` in the status payload when changing throughput.
4. Review queue pressure with `autonomy report summary --json --profile default`.
5. Run one cycle or start the daemon.
6. Tail logs only when `dispatcher workers` points to a suspect task/run.
7. Review `pending_review` aging before ending the session.

Task reference rule:

- Treat autonomy task state as legacy runtime evidence only. For planner-owned scope and acceptance, use CENTRAL DB-backed task state first.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
