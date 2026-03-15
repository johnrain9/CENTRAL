# Voice PTT

`voice-ptt` is the Linux v1/reference workstation-local background daemon for direct voice input into terminal and chat apps.
This is the `CENTRAL-OPS-27` implementation. Portable v2 architecture and Windows/WSL notes now live in `/home/cobra/CENTRAL/docs/voice_ptt_v2.md`.

## What It Does

- Registers a global `Ctrl+Shift+R` toggle in X11/XWayland sessions.
- Plays one beep on start and a different beep on stop.
- Records microphone audio with `ffmpeg`.
- Transcribes the captured WAV through a configurable backend.
- Owns the X11 clipboard and pastes into the focused app with a synthetic paste shortcut.
- Logs to `/tmp/voice-ptt.log` and keeps a single resident process.

## Repo Layout

- Daemon: `/home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py`
- Config: `/home/cobra/CENTRAL/tools/voice_ptt/config.toml`
- User service template: `/home/cobra/CENTRAL/.ops/systemd/voice-ptt.service`
- Login launcher: `/home/cobra/CENTRAL/.ops/bin/voice-ptt-launch`
- Autostart entry: `/home/cobra/CENTRAL/.ops/autostart/voice-ptt.desktop`
- Repair script: `/home/cobra/CENTRAL/scripts/install_voice_ptt.sh`

## Startup Wiring

The install flow uses one reliable startup path:

1. A desktop autostart entry that calls `voice-ptt-launch` at login.
2. `voice-ptt-launch` imports the live graphical environment into the user `systemd` manager and starts `voice-ptt.service`.
3. Once started, the service keeps `Restart=always` for crash recovery inside that login session.

The default-target user-service symlink is intentionally removed during install. That older wiring caused pre-login restart loops on machines where the user manager came up before X11/XWayland access was actually available.

The launcher tries `systemctl --user start voice-ptt.service` first. If the user bus is not ready yet, it falls back to starting the daemon directly. The daemon uses `/tmp/voice-ptt.lock` so duplicate launches collapse to one live instance. The launcher also runs `systemctl --user reset-failed voice-ptt.service` before start so stale failures from earlier boot attempts do not block login startup.

When login autostart launches `voice-ptt-launch`, it imports the current graphical session environment into the user `systemd` manager. That makes `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, and `OPENAI_API_KEY` available to the resident daemon when those variables exist in the login session.

Refresh the live wiring with:

```bash
/home/cobra/CENTRAL/scripts/install_voice_ptt.sh
```

That script links:

- `~/.config/systemd/user/voice-ptt.service`
- `~/.config/autostart/voice-ptt.desktop`
- `~/.local/bin/voice-ptt-launch`

## Backend Configuration

Edit `/home/cobra/CENTRAL/tools/voice_ptt/config.toml`.

### OpenAI backend

Default backend:

```toml
[backend]
type = "openai"

[backend.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-transcribe"
api_key_env = "OPENAI_API_KEY"
api_key_file = ""
```

Options:

- Set `OPENAI_API_KEY` in the environment before the daemon starts.
- Or put `OPENAI_API_KEY=...` in `~/.config/voice-ptt.env` so the user service can load it on every login.
- Or set `api_key_file` to a local file containing only the key.
- Change `model` or `base_url` if the workstation should hit a different compatible endpoint.

### Command backend

Switch to a local or custom transcription command:

```toml
[backend]
type = "command"

[backend.command]
shell_command = "/path/to/transcriber {audio_path}"
trim_stdout = true
```

The command must print the final transcript to stdout.

## Paste Behavior

`[paste].mode` controls the synthetic key sequence after clipboard ownership is updated.

Supported values:

- `ctrl_shift_v` for most terminal emulators
- `ctrl_v` for browser and editor inputs
- `shift_insert` for terminal setups that prefer classic paste

Default is `ctrl_shift_v` because the target workflow is terminal/chat entry.

## Manual Checks

Dependency and X11 readiness:

```bash
python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --self-check
```

`--self-check` now reports:

- command availability for `ffmpeg`, `paplay`, and the active backend client
- whether `DISPLAY` is set
- whether `XAUTHORITY` and `WAYLAND_DISPLAY` are set
- whether the daemon can connect to X11/XWayland
- whether the OpenAI API key is visible through `api_key_env` or `api_key_file`

If `DISPLAY` is present but X11 still fails, the tool now calls that out as a desktop-access problem rather than a generic missing-display problem. On this workstation that exact failure mode showed up with `DISPLAY=:0` present but no working X11/XWayland connection.

Foreground debug run:

```bash
python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --verbose
```

Service log:

```bash
tail -f /tmp/voice-ptt.log
```

## End-to-End Use

1. Focus the terminal or chat input where the transcript should land.
2. Press `Ctrl+Shift+R`.
3. Wait for the start beep, then speak.
4. Press `Ctrl+Shift+R` again.
5. Wait for the stop beep and transcription round-trip.
6. The daemon copies the transcript to the X11 clipboard and sends the configured paste shortcut into the focused app.

## Dependency Assumptions

- `python3`
- `ffmpeg`
- `paplay`
- `curl` for the default OpenAI backend
- An X11/XWayland session with synthetic input allowed
- Microphone capture through PulseAudio/PipeWire's `default` source

No extra Python packages are required.

## Tested 2026-03-15

Validated from `/home/cobra/CENTRAL` on the Linux workstation:

- `python3 -m unittest tests.test_voice_ptt_v2` passed.
- `python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --self-check` confirmed `ffmpeg`, `paplay`, and `curl` were present.
- The resident service wiring existed at `~/.config/systemd/user/voice-ptt.service` and `~/.local/bin/voice-ptt-launch`.
- The prior service log showed a restart loop caused by repeated `Unable to open DISPLAY for global hotkey control` failures before desktop access was available.

Not validated end to end on 2026-03-15 from this shell:

- start beep
- stop beep
- live microphone recording
- real transcript round-trip
- clipboard ownership
- synthetic paste into a focused app

Exact blocker: this shell had `DISPLAY=:0`, but both `xdpyinfo -display :0` and the daemon self-check failed to open X11, and `~/.Xauthority` was absent. That means the daemon could not reach the active desktop from the available environment, so hotkey/clipboard/paste could not be field-validated here.

## Troubleshooting

- `Unable to open DISPLAY for global hotkey control`
  - The daemon needs a live X11/XWayland display. Start it from the graphical session, not a headless shell.
  - If `DISPLAY` is already set, run `python3 /home/cobra/CENTRAL/tools/voice_ptt/voice_ptt.py --self-check` and confirm whether `XAUTHORITY` or X11/XWayland access is missing.
- `voice-ptt daemon is already running`
  - Another instance owns `/tmp/voice-ptt.lock`. Stop the existing process or remove the stale lock after verifying no daemon is live.
- `OpenAI backend selected but no API key was found`
  - Export `OPENAI_API_KEY` before session startup, put it in `~/.config/voice-ptt.env`, or point `api_key_file` at a readable secret file.
- Recording starts but no transcript is pasted
  - Check `/tmp/voice-ptt.log`, then try a different `[paste].mode`.
- Pasted text lands in the wrong app
  - Keep focus on the target window until transcription completes. This utility pastes into whatever window is focused at paste time.
- The daemon does not survive a crash
  - Re-run `/home/cobra/CENTRAL/scripts/install_voice_ptt.sh` and inspect `~/.config/systemd/user/voice-ptt.service` plus `/tmp/voice-ptt.log`.
