# autonomy-planner

Purpose:

- Turn planning intent into executable autonomy tasks with explicit dependencies.
- For canonical planning, use CENTRAL DB state and the CENTRAL task CLI first.
- Use autonomy task commands only for legacy autonomy-DB queues that still need migration support.

Bootstrap contract:

- Activate `/home/cobra/photo_auto_tagging/.venv` before using `autonomy ...`
- If the console script is missing, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Create tasks with clear prompt bodies, repo roots, and validation notes.
- Keep transitions explicit from `draft` to `pending`.
- Maintain dependency edges.
- Update CENTRAL DB first, then refresh generated summaries or exports if needed.

Command dependencies:

- `autonomy task create`
- `autonomy task update`
- `autonomy task list --json`
- `autonomy task show <id> --json`
- `autonomy task set-dependencies`
- `autonomy task start <id>`
- `autonomy task eligible --json`
- `autonomy task blocked --json`
- `autonomy graph list --json`
- `autonomy graph show <id> --json`

Workflow:

1. Inspect or update the canonical task in CENTRAL DB with [`scripts/central_task_db.py`](/home/cobra/CENTRAL/scripts/central_task_db.py).
   For numbering, use `task-id-next` or `task-id-reserve` instead of repeated `task-show` existence probes.
2. Draft the autonomy prompt body from canonical CENTRAL DB state only if a legacy autonomy queue still needs it.
3. Create or update it in autonomy DB only for migration or compatibility work.
4. Set dependencies before promotion.
5. Promote only runnable tasks to `pending`.
6. Reconcile execution outcome back into CENTRAL DB, then update generated or mirror surfaces.

Task reference rule:

- When a planner-owned task has a stable CENTRAL ID, use that ID in notes and query CENTRAL DB-backed task state before consulting repo-local boards or exported markdown.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
