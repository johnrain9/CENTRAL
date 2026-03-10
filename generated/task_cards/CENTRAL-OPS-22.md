# CENTRAL-OPS-22 Cut over dispatcher launcher and operator workflow to CENTRAL-native runtime

Generated from CENTRAL DB at 2026-03-10T14:55:32+00:00. Do not edit manually.

## Metadata
- Task ID: `CENTRAL-OPS-22`
- Planner Status: `todo`
- Runtime Status: `none`
- Priority: `6`
- Target Repo: `CENTRAL` (/home/cobra/CENTRAL)
- Planner Owner: `planner/coordinator`
- Worker Owner: `unassigned`

## Objective
Switch the operator entrypoint over to the new CENTRAL-native runtime once the daemon and worker execution bridge exist. After this task, `dispatcher` should start the CENTRAL-native dispatcher, and operator docs should treat the legacy autonomy dispatcher as fallback or deprecated.

## Context
Today `~/.zshrc` calls `/home/cobra/CENTRAL/scripts/dispatcher_control.py`, but that wrapper still launches `autonomy dispatch daemon` from `photo_auto_tagging`. Once `CENTRAL-OPS-20` and `CENTRAL-OPS-21` are complete, the launcher and operator docs need to point at the CENTRAL-native runtime so the user can actually use the new dispatcher from the normal `dispatcher` command.

## Scope
In scope: update launcher/control script, shell workflow expectations, operator docs, status/log commands, and any compatibility messaging. Out of scope: fixing unrelated legacy autonomy bugs unless they block cutover verification.

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

## Dispatch
Dispatch from CENTRAL using `repo=CENTRAL do task CENTRAL-OPS-22`. Implementation work belongs primarily in `/home/cobra/CENTRAL`, with shell and doc touchpoints as needed.

## Dependencies
- `CENTRAL-OPS-20` (todo) - Implement CENTRAL-native dispatcher daemon loop
- `CENTRAL-OPS-21` (todo) - Implement CENTRAL-native worker execution bridge

## Reconciliation
CENTRAL DB is the canonical planner/runtime store for this task. Reconcile worker outcomes in CENTRAL first. Update shell/operator docs only as required by the cutover.
