# CENTRAL-OPS-25 Add per-repo markdown export generation

## Task Metadata

- `Task ID`: `CENTRAL-OPS-25`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `18`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Add DB-native per-repo markdown export generation so operators can refresh repo-specific queue views directly from CENTRAL DB state.

## Context

- `CENTRAL-OPS-16` defined per-repo queue views as part of the generated-surface contract.
- `CENTRAL-OPS-24` added a one-shot markdown export bundle, but per-repo markdown files were still missing.
- Operators still benefit from repo-specific markdown views for sharing or targeted scans, as long as those files remain generated and non-canonical.

## Scope Boundaries

- Implement per-repo markdown export generation only.
- Do not reintroduce markdown as canonical planner truth.
- Do not change planner/runtime lifecycle semantics.

## Deliverables

1. Add a CLI command that exports one repo-specific markdown queue view.
2. Extend the markdown bundle command to generate per-repo markdown files.
3. Mark all per-repo exports as generated and non-canonical.
4. Document the new export surface.

## Acceptance

1. Operators can generate `generated/per_repo/<repo_id>.md` from CENTRAL DB state with one command.
2. The bundle command also emits per-repo markdown exports.
3. Per-repo exports remain derived outputs only.

## Testing

- Manual review of the per-repo export implementation
- Manual review of the updated CLI documentation
- Minimal smoke verification complete on 2026-03-10:
  - `python3 /home/cobra/CENTRAL/scripts/central_task_db.py export-repo-md --repo-id CENTRAL --json`

## Dependencies

- Soft prerequisites: `CENTRAL-OPS-16`, `CENTRAL-OPS-24`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-25`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-25 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB remains the authoritative planning system.
- Per-repo markdown exports remain derived outputs only.

## Validation Rules

- filename matches `CENTRAL-OPS-25`
- required sections are present
