# CENTRAL-OPS-27 Implement always-on push-to-talk voice transcription utility for terminal/chat input

Generated from CENTRAL DB at 2026-03-12T15:09:30+00:00. Do not edit manually.

## Metadata
- Task ID: `CENTRAL-OPS-27`
- Planner Status: `done`
- Runtime Status: `running`
- Priority: `8`
- Target Repo: `CENTRAL` (/home/cobra/CENTRAL)
- Planner Owner: `planner/coordinator`
- Worker Owner: `unassigned`

## Objective
Implement a workstation-local voice transcription tool that is always available while the computer is on. The user should be able to press `Ctrl+Shift+R` once to hear a start beep and begin recording, press `Ctrl+Shift+R` again to hear a different stop beep and end recording, then automatically transcribe the audio, copy the transcript to the clipboard, and paste it into the active application so the user can talk directly to Codex/ChatGPT in the terminal.

## Context
The user wants a frictionless voice-to-terminal workflow on this machine. The tool must behave like a push-to-talk/toggle recorder that is always resident in the background when the computer is on. It needs to integrate with desktop hotkeys, microphone capture, transcription, clipboard management, and synthetic paste into the focused window. Reliability and low-friction interaction matter more than portability. Partial implementation work already exists from a prior interrupted worker run. The next worker must inspect and continue that work rather than starting from scratch.

## Scope
In scope: a user-session background service or equivalent auto-start mechanism, global hotkey capture for `Ctrl+Shift+R`, distinct audible start/stop cues, microphone recording, transcription, clipboard copy, synthetic paste into the active window, configuration for transcription backend, and operator docs for install/startup/troubleshooting. Out of scope: mobile support, cross-platform packaging beyond this workstation, and manual one-off recording flows that do not run continuously in the background. Recovery instruction: treat existing implementation artifacts as the current working state and continue from them unless a specific file is clearly unusable.

## Deliverables
1. An implementation of the background voice transcription utility in `/home/cobra/CENTRAL` or a clearly justified subpath.
2. User-session startup wiring so the tool is active whenever the computer is on and the user session starts.
3. Global `Ctrl+Shift+R` toggle behavior with one beep on start and a different beep on stop.
4. Audio capture plus transcription pipeline with configurable backend details.
5. Clipboard copy and active-window paste integration after transcription completes.
6. Documentation covering install, dependency assumptions, startup behavior, backend configuration, and recovery/troubleshooting.
7. Continue from the existing partial implementation rather than rebuilding the task from zero.

## Acceptance
1. The tool starts automatically for the user session and remains available in the background without manual launch each login.
2. Pressing `Ctrl+Shift+R` starts recording and emits a clear start beep.
3. Pressing `Ctrl+Shift+R` again stops recording and emits a distinct stop beep.
4. After stop, the captured audio is transcribed and the resulting text is copied to the clipboard and pasted into the currently focused terminal/chat input.
5. Failures produce a visible or logged error path that does not leave the recorder stuck in a bad state.
6. The setup is documented well enough to reinstall or repair on this machine.

## Testing
- Manual end-to-end test: trigger start/stop with `Ctrl+Shift+R`, verify both beeps, verify transcript content, verify clipboard contents, and verify pasted output in an active terminal input.
- Restart/login test: confirm the background service is active after session start without manual intervention.
- Failure-path test: verify microphone or backend failure produces a recoverable/logged error and a clean next recording attempt.
- Record concrete dependency/backend choices in the closeout notes.
- Verify the resumed implementation correctly picks up prior partial work rather than overwriting it blindly.

## Dispatch
Dispatch from CENTRAL using `repo=CENTRAL do task CENTRAL-OPS-27`. Implementation work belongs on this workstation and should land under `/home/cobra/CENTRAL` unless the worker establishes a better repo-local home during implementation and documents the decision.

## Dependencies
- none

## Reconciliation
CENTRAL DB is the source of truth. Reconcile implementation results in CENTRAL first, then refresh any generated views or optional docs mirrors.
