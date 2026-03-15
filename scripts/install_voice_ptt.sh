#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/cobra/CENTRAL"
UNIT_SOURCE="$ROOT/.ops/systemd/voice-ptt.service"
AUTOSTART_SOURCE="$ROOT/.ops/autostart/voice-ptt.desktop"
LAUNCHER_SOURCE="$ROOT/.ops/bin/voice-ptt-launch"

mkdir -p "$HOME/.config/systemd/user"
mkdir -p "$HOME/.config/autostart"
mkdir -p "$HOME/.local/bin"

ln -sfn "$UNIT_SOURCE" "$HOME/.config/systemd/user/voice-ptt.service"
ln -sfn "$AUTOSTART_SOURCE" "$HOME/.config/autostart/voice-ptt.desktop"
ln -sfn "$LAUNCHER_SOURCE" "$HOME/.local/bin/voice-ptt-launch"
rm -f "$HOME/.config/systemd/user/default.target.wants/voice-ptt.service"

chmod +x "$LAUNCHER_SOURCE" "$ROOT/tools/voice_ptt/voice_ptt.py"

if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user daemon-reload >/dev/null 2>&1; then
    systemctl --user disable voice-ptt.service >/dev/null 2>&1 || true
  fi
fi

echo "voice-ptt install wiring refreshed"
