# CENTRAL-OPS-19 Retire markdown-first bridge paths and non-canonical manual task maintenance

## Task Metadata

- `Task ID`: `CENTRAL-OPS-19`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `migration`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: transitional bootstrap snapshot only; DB-canonical model supersedes markdown
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `15`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `["/home/cobra/photo_auto_tagging"]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Finish the transition away from markdown-first task management by retiring bridge-first assumptions, stopping manual canonical markdown maintenance, and leaving only DB-native operation plus optional generated exports.

## Context

- The DB, planner CRUD, generated views, dispatcher integration, and migration need to exist first.
- Transitional bootstrap tools should not become permanent architecture.
- This task is the cutover and cleanup point.

## Scope Boundaries

- Retire or demote markdown-first paths and update docs/skills accordingly.
- Do not redesign the architecture again in this task.

## Deliverables

1. Retire `autonomy central sync` or equivalent markdown-first paths as primary workflow.
2. Update docs and skills so DB-native planning and runtime operation are the canonical path.
3. Remove or demote any remaining manual canonical markdown maintenance expectations.
4. Preserve optional import/export or archival tooling only where still useful.

## Acceptance

1. DB-native planning and dispatch are the documented primary workflow.
2. Operators are no longer expected to maintain canonical task state in markdown.
3. Transitional bridge paths are clearly marked deprecated, retired, or import-only.

## Testing

- Manual review of updated docs and skill surfaces
- Verify primary operator/planner commands point at DB-native workflow
- Verify optional export/import paths remain clearly non-canonical
- Manual review complete on 2026-03-10:
  - skill docs and canonical autonomy runbooks now point to [`central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md) for canonical CENTRAL workflows
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md) now treats `autonomy central sync` as deprecated import-only
  - legacy autonomy CLI help now warns that `central sync` is deprecated and import-only

Review result:
- accepted CENTRAL DB as the documented primary workflow for planning, runtime, and generated views
- accepted markdown task files and summaries as bootstrap, export, or archival surfaces only

## Dependencies

- `CENTRAL-OPS-15`
- `CENTRAL-OPS-16`
- `CENTRAL-OPS-17`
- `CENTRAL-OPS-18`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-19`.
- Implementation work belongs primarily in `/home/cobra/CENTRAL`; update external runtime docs only where required.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-19 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the authoritative planning system.
- Markdown surfaces should remain optional exports, imports, or archival material only after this task.
- Implementation now lives in:
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)
  - [`docs/central_task_cli.md`](/home/cobra/CENTRAL/docs/central_task_cli.md)
  - [`docs/autonomy_skills/README.md`](/home/cobra/CENTRAL/docs/autonomy_skills/README.md)
  - [`docs/autonomy_skills/autonomy-planner.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-planner.md)
  - [`docs/autonomy_skills/autonomy-operator.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-operator.md)
  - [`docs/autonomy_skills/autonomy-triage.md`](/home/cobra/CENTRAL/docs/autonomy_skills/autonomy-triage.md)
  - [`/home/cobra/.codex/skills/autonomy-planner/SKILL.md`](/home/cobra/.codex/skills/autonomy-planner/SKILL.md)
  - [`/home/cobra/.codex/skills/autonomy-operator/SKILL.md`](/home/cobra/.codex/skills/autonomy-operator/SKILL.md)
  - [`/home/cobra/.codex/skills/autonomy-triage/SKILL.md`](/home/cobra/.codex/skills/autonomy-triage/SKILL.md)
  - [`/home/cobra/photo_auto_tagging/autonomy/cli.py`](/home/cobra/photo_auto_tagging/autonomy/cli.py)
  - [`/home/cobra/photo_auto_tagging/autonomy/central_sync.py`](/home/cobra/photo_auto_tagging/autonomy/central_sync.py)

## Validation Rules

- filename matches `CENTRAL-OPS-19`
- required sections are present
