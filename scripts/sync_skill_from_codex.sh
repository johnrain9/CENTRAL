#!/usr/bin/env bash
set -euo pipefail

SRC_BASE="${1:-/home/cobra/.codex/skills/multi-repo-planner}"
DST_BASE="${2:-/home/cobra/CENTRAL/skills/multi-repo-planner}"

mkdir -p "$DST_BASE/references" "$DST_BASE/agents"
cp "$SRC_BASE/SKILL.md" "$DST_BASE/SKILL.md"
cp "$SRC_BASE/references/dispatch-and-status.md" "$DST_BASE/references/dispatch-and-status.md"
cp "$SRC_BASE/agents/openai.yaml" "$DST_BASE/agents/openai.yaml"

echo "Synced multi-repo-planner skill to: $DST_BASE"
