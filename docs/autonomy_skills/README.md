# Autonomy Skills

This directory is the canonical documentation home for autonomy operations, planning, and triage.
For canonical CENTRAL task authoring, lifecycle control, generated views, and runtime state, use the CENTRAL DB CLI documented in [`../central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md).

Core docs:

- [`autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
- [`autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
- [`autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
- [`../dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)

Ownership rules:

- `CENTRAL` is the long-term source of truth for autonomy runbooks and skill-facing docs.
- CENTRAL SQLite DB is the canonical source of truth for planner-owned work.
- markdown task files and summaries are bootstrap, generated, import, export, or archival surfaces only.
- `/home/cobra/.codex/skills/autonomy-*` should reference these docs.
- `/home/cobra/photo_auto_tagging/docs/autonomy_skills/` is implementation-local only and should remain minimal.
