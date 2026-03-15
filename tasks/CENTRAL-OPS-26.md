# CENTRAL-OPS-26 Add CENTRAL-native runtime self-check command

## Task Metadata

- `Task ID`: `CENTRAL-OPS-26`
- `Status`: `done`
- `Target Repo`: `/home/cobra/CENTRAL`
- `Task Type`: `implementation`
- `Planner Owner`: `planner/coordinator`
- `Worker Owner`: `planner/coordinator`
- `Source Of Truth`: CENTRAL DB canonical record; this file is a bootstrap snapshot only
- `Summary Record`: [`tasks.md`](/home/cobra/CENTRAL/tasks.md)

## Execution Settings

- `Priority`: `7`
- `Task Kind`: `mutating`
- `Sandbox Mode`: `workspace-write`
- `Approval Policy`: `never`
- `Additional Writable Dirs`: `[]`
- `Timeout Seconds`: `1800`
- `Approval Required`: `false`

## Objective

Add a CENTRAL-native runtime self-check command that exercises the daemon and worker bridge against a temporary DB in stub mode.

## Context

- `CENTRAL-OPS-20` added the daemon loop.
- `CENTRAL-OPS-21` added the worker execution bridge.
- `CENTRAL-OPS-22` cut the launcher over to the CENTRAL-native runtime.
- Operators and future tasks need one deterministic command that proves the runtime stack still boots and executes a synthetic task end to end without touching live state.

## Scope Boundaries

- Implement self-check against a temporary DB and stub worker mode only.
- Do not mutate the live CENTRAL DB as part of the self-check.
- Do not reintroduce legacy autonomy dispatcher dependence.

## Deliverables

1. Add a CENTRAL-native self-check command.
2. Create a temporary DB and synthetic smoke task inside the self-check.
3. Execute the runtime path in stub worker mode and return structured results.
4. Document the command for operator use.

## Acceptance

1. Operators can run one command to verify the CENTRAL-native runtime path end to end.
2. The self-check uses isolated temporary state rather than live planner/runtime state.
3. The self-check proves daemon/worker-bridge plumbing without requiring the legacy autonomy dispatcher.

## Testing

- Minimal smoke verification complete on 2026-03-10:
  - `python3 /home/cobra/CENTRAL/scripts/central_runtime.py self-check`

Review result:
- accepted `self-check` as the canonical runtime smoke path for CENTRAL-native dispatcher validation
- accepted temporary DB plus stub worker mode as the right isolation model for operator verification

## Dependencies

- `CENTRAL-OPS-20`
- `CENTRAL-OPS-21`
- `CENTRAL-OPS-22`

## Dispatch Contract

- Dispatch from `CENTRAL` using `repo=CENTRAL do task CENTRAL-OPS-26`.
- Implementation work belongs in `/home/cobra/CENTRAL`.

## Closeout Contract

Required closeout line:

```text
CENTRAL-OPS-26 | done|blocked | tests: <cmd/result> | ref: <branch/commit/notes>
```

## Repo Reconciliation

- CENTRAL DB remains the canonical planner/runtime store.
- The self-check remains an operator validation tool, not a planner workflow.
- Implementation now lives in:
  - [`scripts/central_runtime.py`](/home/cobra/CENTRAL/scripts/central_runtime.py)
  - [`dispatch_system_readme.md`](/home/cobra/CENTRAL/dispatch_system_readme.md)

## Validation Rules

- filename matches `CENTRAL-OPS-26`
- required sections are present
