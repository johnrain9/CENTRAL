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
if [ -z "${CENTRAL_COORDINATION_TOKEN:-}" ]; then
    echo "❌ CENTRAL_COORDINATION_TOKEN is required for remote worker mode."
    echo "Set it in your shell/environment before starting:"
    echo "  export CENTRAL_COORDINATION_TOKEN=\"...\""
    exit 1
fi
export CENTRAL_COORDINATION_TOKEN
PYTHONPATH=scripts python3 -m central_runtime_v2 daemon --remote-workers
