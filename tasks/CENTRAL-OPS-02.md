# CENTRAL-OPS-02 Update planner skills and dispatch contracts for CENTRAL-as-source-of-truth

## Task Metadata

- `Task ID`: `CENTRAL-OPS-02`
- `Status`: `done`
- `Target Repo`: `/home/cobra/.codex/skills`
- `Task Type`: `migration`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: this file
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)
- `Bootstrap Packet`: [`central_task_system_tasks.md`](/home/cobra/CENTRAL/central_task_system_tasks.md)

## Objective

Update planner/operator skills and CENTRAL runbooks so planner-owned tasks are authored in `CENTRAL`, with repo-local boards treated as optional mirrors or intake only.

## Context

- `CENTRAL-OPS-01` froze `CENTRAL/tasks/<TASK_ID>.md` as the canonical planner-owned task format.
- Existing planner guidance still described repo-local task boards as execution truth or primary planning intake.
- The packaged skills under `/home/cobra/.codex/skills` and the CENTRAL autonomy docs needed the same ownership model.

## Scope Boundaries

- Update skill contracts and supporting CENTRAL runbooks to use `CENTRAL` canonical tasks.
- Define how planner, operator, and triage flows should reference CENTRAL-owned task IDs.
- Keep repo-local boards available as optional mirrors or local intake during migration.
- Do not implement the CENTRAL-to-autonomy ingestion bridge in this task.

## Deliverables

1. Update `multi-repo-planner` to treat `CENTRAL` canonical task files as execution truth for planner-owned work.
2. Update planner/operator/triage skill language so CENTRAL-owned task IDs are the reference point when task context is needed.
3. Update CENTRAL runbooks and summary docs to match the new ownership model.
4. Reconcile the summary index and bootstrap packet to point at the canonical task record for this task.

## Acceptance

1. Skill docs no longer claim repo-local boards are the default source of truth for planner-owned execution tasks.
2. CENTRAL docs and packaged skills agree that planner-owned task creation and updates happen in `CENTRAL`.
3. Dispatch guidance is explicit that the worker reads `CENTRAL/tasks/<TASK_ID>.md` first and uses `Target Repo` to decide where implementation work belongs.

## Testing

- `python3 /home/cobra/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/cobra/.codex/skills/multi-repo-planner`
- `python3 /home/cobra/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/cobra/.codex/skills/autonomy-planner`
- Manual review of [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)

## Dependencies

- `CENTRAL-OPS-01`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-02`.
- Read this file as the canonical task body.
- Apply implementation/doc changes in the skill repo and CENTRAL docs referenced here.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-02 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

Blocked rule:

- If blocked, include exactly one concrete unblocker request.

## Repo Reconciliation

- Update the packaged skill files under `/home/cobra/.codex/skills`.
- Update CENTRAL docs and summary records after the skill contract changes are complete.
- Treat repo-local boards as mirrors only where still needed for migration context.

## Validation Rules

- filename matches `CENTRAL-OPS-02`
- all required sections are present
- `Status` uses the canonical lifecycle set
- [`tasks.md`](/home/cobra/CENTRAL/tasks.md) points back to this file
