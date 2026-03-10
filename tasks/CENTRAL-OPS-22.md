# CENTRAL-OPS-22 Cut over dispatcher launcher and operator workflow to CENTRAL-native runtime

## Task Metadata

- `Task ID`: `CENTRAL-OPS-22`
- `Status`: `todo`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `unassigned`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `6`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `["/home/cobra"]`
- `Timeout Seconds`: `3600`
- `Approval Required`: `false`

## Objective

Switch the operator entrypoint over to the new CENTRAL-native runtime once the daemon and worker execution bridge exist.

## Context

- `~/.zshrc` calls `/home/cobra/CENTRAL/scripts/dispatcher_control.py`.
- That wrapper still launches `autonomy dispatch daemon` from `photo_auto_tagging`.
- Once `CENTRAL-OPS-20` and `CENTRAL-OPS-21` are complete, the launcher and operator docs need to point at the CENTRAL-native runtime.

## Scope Boundaries

- Update launcher/control script, shell workflow expectations, operator docs, status/log commands, and compatibility messaging.
- Do not fix unrelated legacy autonomy bugs unless they block cutover verification.

## Deliverables

1. Update the dispatcher launcher/control path to use CENTRAL-native runtime commands.
2. Update operator docs and skill references for the new startup/status/log workflow.
3. Preserve clear fallback or deprecation messaging for the legacy autonomy dispatcher if it remains available.
4. Verify the normal `dispatcher` command uses the CENTRAL-native path.

## Acceptance

1. `dispatcher` starts the CENTRAL-native runtime instead of `autonomy dispatch daemon`.
2. Operator docs and launcher behavior match the actual runtime path.
3. Status/log/follow workflows work through the updated launcher.
4. Legacy autonomy dispatcher usage is clearly demoted to fallback or deprecated status.

## Testing

- Run `dispatcher`, `dispatcher status`, and `dispatcher logs` against the CENTRAL-native runtime.
- Verify the launcher no longer shells into legacy autonomy dispatch as the primary path.
- Verify docs and launcher messages match the actual commands used.

## Dependencies

- `CENTRAL-OPS-20`
- `CENTRAL-OPS-21`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-22`.
- Implementation work belongs primarily in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-22 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB is the canonical planner/runtime store for this task.
- Reconcile worker outcomes in CENTRAL first.

## Validation Rules

- filename matches `CENTRAL-OPS-22`
- required sections are present
