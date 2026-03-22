#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <skill-name> [target-name]" >&2
  exit 2
fi

SKILL_NAME="$1"
TARGET_NAME="${2:-$SKILL_NAME}"
SRC_DIR="$REPO_ROOT/skills/$SKILL_NAME"
DST_DIR="$CODEX_HOME/skills/$TARGET_NAME"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Skill source not found: $SRC_DIR" >&2
  exit 1
fi

mkdir -p "$DST_DIR"
cp "$SRC_DIR/SKILL.md" "$DST_DIR/SKILL.md"

for subdir in agents references scripts assets; do
  if [[ -d "$SRC_DIR/$subdir" ]]; then
    mkdir -p "$DST_DIR/$subdir"
    cp -R "$SRC_DIR/$subdir/." "$DST_DIR/$subdir/"
  fi
done

echo "Installed Codex skill: $TARGET_NAME -> $DST_DIR"
