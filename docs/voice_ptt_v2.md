# Voice PTT Portable V2

`CENTRAL-OPS-32` builds the portable v2 architecture under `/home/cobra/CENTRAL/tools/voice_ptt_v2`.
This is a new split between shared orchestration and platform adapters. It does not replace the macOS production runtime in `/home/cobra/voice_transcription/voice`.

## V1 Versus V2

- `CENTRAL-OPS-27` remains the Linux v1/reference workflow.
- Linux v1 behavior still enters through `/home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py`, but that file is now only a compatibility wrapper around the v2 Linux adapter.
- v2 moves config normalization, transcription orchestration, structured logging, and machine-readable results into a shared Python core.
- v2 keeps hotkey, recording, clipboard, paste, and startup behavior in platform adapters.

## Module Split

```text
tools/voice_ptt_v2/
  cli.py                      Shared entrypoint for file-based transcription
  core/
    config.py                 Shared config and legacy-v1 normalization
    controller.py             Session orchestration and structured results
    backends.py               Backend abstraction for OpenAI or command runners
    contracts.py              Session/result contracts
    logging_utils.py          Structured logging helpers
  adapters/
    linux.py                  X11 hotkey + Pulse/PipeWire + clipboard/paste
    windows/
      voice_ptt_hotkey.ahk    Native desktop wrapper for Windows
      install_startup.ps1     Startup-folder installer
    wsl/
      bridge.py               Helper-only JSON bridge for `wsl.exe`
```

## Shared Core Responsibilities

The shared core owns:

- config loading and legacy v1 compatibility mapping
- transcription backend selection
- session IDs, timing, and structured success/error results
- machine-readable JSON output for file-based transcription
- structured log events for started/finished/failed transcription work

The shared core does not directly depend on:

- X11
- `skhd`
- `pbcopy`
- `osascript`
- AutoHotkey
- user `systemd` startup wiring

## Linux Adapter

Linux remains an adapter, not the shared runtime model.

- Entry point: `/home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py`
- Adapter implementation: `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/linux.py`
- Responsibilities:
  - global `Ctrl+Shift+R` hotkey via X11/XTest
  - `ffmpeg` microphone capture from PulseAudio/PipeWire
  - X11 clipboard ownership
  - synthetic paste into the focused app
  - existing login/startup wiring through `.ops/` and `install_voice_ptt.sh`

This preserves the Linux v1 UX while keeping X11-specific behavior out of the shared core.

## Windows-Native Adapter

Windows is a first-class native desktop path. It is not designed to run from WSL.

- Hotkey/desktop wrapper: `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/windows/voice_ptt_hotkey.ahk`
- Startup registration helper: `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/windows/install_startup.ps1`
- Default responsibilities owned on Windows:
  - global hotkey registration through AutoHotkey
  - microphone capture with Windows `ffmpeg`/DirectShow
  - clipboard copy on the Windows desktop
  - optional `Ctrl+V` paste on the Windows desktop
  - startup registration through the Windows Startup folder
- Shared core role:
  - transcribe the recorded audio file
  - emit JSON result and transcript text files

### Windows install path

1. Install Python, `ffmpeg`, and AutoHotkey v2 on Windows.
2. Copy or clone `CENTRAL` to a Windows path such as `C:\CENTRAL`.
3. Edit `voice_ptt_hotkey.ahk` for local `RepoRoot`, microphone device name, and preferred hotkey.
4. Run `ffmpeg -list_devices true -f dshow -i dummy` in Windows PowerShell and copy the exact microphone name into `AudioInput`.
5. Run `install_startup.ps1` if the wrapper should auto-start at login.
5. Launch `voice_ptt_hotkey.ahk` manually for the first desktop validation.

### Windows runtime contract

The current Windows wrapper still uses the editable constants at the top of `voice_ptt_hotkey.ahk`. It does not read `[platforms.windows]` from `config.toml` yet. The actual live inputs are:

- `PythonExe`
- `RepoRoot`
- `ConfigPath`
- `FfmpegPath`
- `AudioInput`
- `ToggleHotkey`
- `PasteEnabled`
- `PasteDelayMs`

`install_startup.ps1` now resolves the AutoHotkey executable from `PATH` or common install locations and fails loudly if the script or executable is missing. `voice_ptt_hotkey.ahk` now also validates `RepoRoot`, `ConfigPath`, `python.exe`, and `ffmpeg.exe` before recording starts, and the DirectShow input is quoted so device names with spaces work correctly.

### Current validation status

The Windows wrapper was reviewed and hardened from this Linux workspace, but it was not validated in a live Windows desktop session on 2026-03-15. Remaining device-specific validation is still required for:

- the exact DirectShow microphone name
- the installed Python executable path
- the installed AutoHotkey v2 executable path
- whether the target app wants `Ctrl+V` or a different paste sequence

## WSL Bridge Contract

WSL2 is helper/backend mode only.

- Bridge entry point: `/home/cobra/CENTRAL/tools/voice_ptt_v2/adapters/wsl/bridge.py`
- Invocation model: `wsl.exe python3 -m tools.voice_ptt_v2.adapters.wsl.bridge`
- Request contract:

```json
{
  "audio_path": "/mnt/c/Users/cobra/AppData/Local/Temp/voice-ptt/capture.wav",
  "requested_by": "windows_wrapper",
  "metadata": {
    "job": "optional"
  }
}
```

- Response contract: the same structured JSON returned by `tools.voice_ptt_v2 cli.py`, with `platform="wsl_bridge"` and `metadata.bridge_mode="helper_only"`.
- Request validation:
  - `audio_path` must be a non-empty string
  - `metadata`, when present, must be a JSON object
  - `requested_by` is normalized to a non-empty string and defaults to `windows_host`

What WSL does not own:

- global Windows hotkeys
- Windows clipboard
- paste into the active Windows app
- Windows startup/login behavior

## Core CLI

The shared core can transcribe a file without desktop dependencies:

```bash
python3 -m tools.voice_ptt_v2 transcribe-file \
  --config /home/cobra/CENTRAL/tools/voice_ptt/config.toml \
  --audio-file /path/to/capture.wav \
  --platform cli \
  --pretty
```

This prints machine-readable JSON and exits non-zero on transcription failure.

## Operator Notes

- Linux operators can continue using `/home/cobra/CENTRAL/scripts/install_voice_ptt.sh`.
- Windows operators should treat AutoHotkey as the desktop host and the Python core as the transcription engine.
- WSL should only be introduced when the Windows wrapper needs Linux-hosted transcription, not for the primary desktop UX.

## Tested 2026-03-15

- Shared-core CLI and WSL bridge contract tests passed via `python3 -m unittest tests.test_voice_ptt_v2`.
- Linux adapter self-check reached dependency validation but could not reach X11/XWayland from the available shell, so desktop hotkey/clipboard/paste behavior remains blocked on a real reachable desktop session.
- Windows and WSL contracts were code-reviewed against the current adapters; Windows desktop execution itself remains unvalidated from this machine.
