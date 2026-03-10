# autonomy-planner

Purpose:

- Turn planning intent into executable autonomy tasks with explicit dependencies.
- Keep autonomy DB as the execution surface while markdown remains intake and mirror state during migration.

Bootstrap contract:

- Activate `/home/cobra/photo_auto_tagging/.venv` before using `autonomy ...`
- If the console script is missing, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Create tasks with clear prompt bodies, repo roots, and validation notes.
- Keep transitions explicit from `draft` to `pending`.
- Maintain dependency edges.
- Mirror status changes back to repo boards and `CENTRAL/tasks.md`.

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

1. Capture work from repo-local boards or `CENTRAL/tasks.md`.
2. Draft the task in memory.
3. Create or update it in autonomy DB.
4. Set dependencies before promotion.
5. Promote only runnable tasks to `pending`.
6. Mirror the result back to markdown after execution or review.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
