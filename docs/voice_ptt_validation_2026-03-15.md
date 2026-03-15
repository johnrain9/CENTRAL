# Voice PTT Validation Report 2026-03-15

Scope: Linux v1 compatibility wrapper, Linux v2 adapter behavior, Windows adapter assumptions, and WSL bridge contract.

## Environment

- Repository: `/home/cobra/CENTRAL`
- Date: `2026-03-15`
- Host shell environment visible during validation:
  - `DISPLAY=:0`
  - `WAYLAND_DISPLAY=wayland-0`
  - `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`
  - `XAUTHORITY` unset

## Linux v1 Compatibility Wrapper

- Entry point validated: `/home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py`
- Result: wrapper is only a compatibility shim into `tools.voice_ptt_v2.adapters.linux.main`, which matches the intended v1-on-v2 split.
- Regression risk checked by unit test: `test_linux_adapter_keeps_desktop_sequence_outside_core`

## Linux v2 Adapter

Validated:

- dependency self-check path
- structured transcription handoff to the shared core
- recording/beep/paste sequencing in unit tests
- startup wiring review for `.ops/bin/voice-ptt-launch`, `.ops/systemd/voice-ptt.service`, and `scripts/install_voice_ptt.sh`

Observed on the workstation:

- `python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --self-check` reported `ffmpeg`, `paplay`, and `curl` as available
- the same self-check failed to open X11/XWayland even with `DISPLAY=:0`
- `xdpyinfo -display :0` also failed
- `/tmp/voice-ptt.log` showed repeated startup failures: `Unable to open DISPLAY for global hotkey control`

Conclusion:

- end-to-end Linux desktop validation was blocked by lack of reachable X11/XWayland access from the available shell
- beeps, hotkey capture, live microphone recording, clipboard ownership, and synthetic paste could not be exercised against the active desktop from this environment

Fixes applied:

- self-check now prints `XAUTHORITY` and `WAYLAND_DISPLAY` and explains the likely X11 access failure mode
- install wiring no longer enables the user service at `default.target`, which had been causing restart loops before desktop access was ready
- `voice-ptt-launch` now resets failed service state before starting the session-local service

Residual risk:

- final Linux desktop verification is still required from a shell or launcher that can actually open the active X11/XWayland display

## Windows Adapter Assumptions

Reviewed files:

- `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/windows/voice_ptt_hotkey.ahk`
- `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/windows/install_startup.ps1`

Validated by inspection and hardening:

- the wrapper is native-desktop-first and does not depend on WSL for hotkey, clipboard, paste, or startup
- the transcription path correctly calls `python -m tools.voice_ptt_v2 transcribe-file`
- DirectShow device names can contain spaces, so the ffmpeg `-i` argument is now quoted
- startup installation now resolves AutoHotkey from `PATH` or common install locations and errors clearly if unresolved
- the wrapper now checks that repo root, config path, Python, ffmpeg, and transcript output exist before continuing

Not validated on a live Windows desktop:

- exact `AudioInput` device string
- exact `PythonExe` path
- exact AutoHotkey v2 install path on the target machine
- paste behavior in the target Windows applications

Residual risk:

- operator must still set the device-specific constants in `voice_ptt_hotkey.ahk` before first use

## WSL Bridge Contract

Validated:

- success path via `test_wsl_bridge_returns_helper_mode_result`
- failure path for missing `audio_path` via `test_wsl_bridge_rejects_missing_audio_path`

Fixes applied:

- the bridge now rejects empty `audio_path`
- the bridge now enforces object-shaped `metadata`
- `requested_by` is normalized to a non-empty string

Conclusion:

- WSL helper mode contract is explicit and machine-checkable
- WSL remains helper-only, not the desktop host

## Commands Run

```bash
python3 -m unittest tests.test_voice_ptt_v2
python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --self-check
env | rg '^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|OPENAI_API_KEY)='
xdpyinfo -display :0
tail -n 40 /tmp/voice-ptt.log
```

## Outcome

- Shared-core and adapter tests: passed
- Linux desktop field validation: blocked by unreachable X11/XWayland from the available environment
- Windows desktop field validation: not available from this machine; assumptions are now documented and checked more explicitly
