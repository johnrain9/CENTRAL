# CENTRAL-OPS-22 Cut over dispatcher launcher and operator workflow to CENTRAL-native runtime

## Task Metadata

- `Task ID`: `CENTRAL-OPS-22`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
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
- Minimal smoke verification complete on 2026-03-10:
  - `dispatcher_control.py` now targets CENTRAL-native runtime commands
  - launcher status/log workflow points at CENTRAL runtime state and logs rather than autonomy dispatcher state

Review result:
- accepted `dispatcher` cutover to the CENTRAL-native runtime as the primary operator path
- accepted legacy autonomy dispatcher as secondary compatibility-only tooling, not the default launcher path

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
- Implementation now lives in:
  - [`scripts/central_runtime.py`](/home/cobra/CENTRAL/scripts/central_runtime.py)
  - [`scripts/dispatcher_control.py`](/home/cobra/CENTRAL/scripts/dispatcher_control.py)
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)

## Validation Rules

- filename matches `CENTRAL-OPS-22`
- required sections are present
