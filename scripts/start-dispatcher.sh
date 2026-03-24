#!/bin/bash
set -euo pipefail
echo "=== CENTRAL Dispatcher with Remote Workers ==="
echo "Date: $(date)"
MAC_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [ -z "$MAC_IP" ]; then
    echo "❌ Could not detect LAN IP."
    exit 1
fi
echo "🌐 Mac IP detected: $MAC_IP"
COORDINATION_PORT=7429
echo "🚀 Starting coordination server on http://$MAC_IP:$COORDINATION_PORT"
cd "$(dirname "$0")/.." || exit 1
export CENTRAL_COORDINATION_URL="http://$MAC_IP:$COORDINATION_PORT"
: "${CENTRAL_COORDINATION_TOKEN:=super-secret-token-2026-change-me}"
export CENTRAL_COORDINATION_TOKEN
export CENTRAL_WORKER_TOKEN="${CENTRAL_WORKER_TOKEN:-$CENTRAL_COORDINATION_TOKEN}"
PYTHONPATH=scripts python3 -m central_runtime_v2 daemon --remote-workers
