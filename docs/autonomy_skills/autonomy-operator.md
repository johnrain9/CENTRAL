# autonomy-operator

Purpose:

- Keep dispatcher and worker fleet healthy during daily operation.
- Provide a deterministic operating rhythm for claim, run, recover, and backlog review.
- Surface stale reviews before they block throughput.

Bootstrap contract:

- Primary shell entrypoint: `dispatcher ...`
- Primary CLI surface inside `/home/cobra/photo_auto_tagging/.venv`: `autonomy ...`
- If `autonomy` is missing after activation, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Start, stop, or pulse the dispatcher.
- Inspect active runs and tail logs.
- Surface completion, failure, and review-aging pressure.
- Hand off approval and rejection decisions to explicit task commands.

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
2. Review queue pressure with `autonomy report summary --json --profile default`.
3. Run one cycle or start the daemon.
4. Monitor workers and logs.
5. Review `pending_review` aging before ending the session.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
