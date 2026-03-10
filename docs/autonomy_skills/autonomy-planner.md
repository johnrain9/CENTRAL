# autonomy-planner

Purpose:

- Turn planning intent into executable autonomy tasks with explicit dependencies.
- Keep `CENTRAL/tasks/<TASK_ID>.md` as the authored source of truth while autonomy DB remains the execution surface during migration.

Bootstrap contract:

- Activate `/home/cobra/photo_auto_tagging/.venv` before using `autonomy ...`
- If the console script is missing, run `./.venv/bin/python -m pip install -e .`
- Use `python -m autonomy.cli ...` only as a fallback
- First-run profile bootstrap: `autonomy init --profile default`

Deterministic responsibilities:

- Create tasks with clear prompt bodies, repo roots, and validation notes.
- Keep transitions explicit from `draft` to `pending`.
- Maintain dependency edges.
- Update the canonical CENTRAL task first, then mirror summary changes to `tasks.md` and any repo-local board if needed.

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

1. Author or refine the task in `CENTRAL/tasks/<TASK_ID>.md`.
2. Draft the autonomy prompt body from that canonical task.
3. Create or update it in autonomy DB.
4. Set dependencies before promotion.
5. Promote only runnable tasks to `pending`.
6. Reconcile execution outcome back into the canonical CENTRAL task, then update summary or mirror surfaces.

Task reference rule:

- When a planner-owned task has a stable CENTRAL ID, use that ID in notes and open the canonical file before consulting repo-local boards.

References:

- [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
