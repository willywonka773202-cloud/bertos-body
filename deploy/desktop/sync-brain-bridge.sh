#!/bin/bash
# Re-vendor the brain MCP bridge from its source of truth (bertosV2) into the
# body's image-bundled copy. Run after editing the bridge in bertosV2.
#
#   bash deploy/desktop/sync-brain-bridge.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${BERTOS_BRAIN_DIR:-$HOME/Documents/bertosV2}/scripts/brain-mcp-server.mjs"
DST="$REPO/brain-bridge/scripts/brain-mcp-server.mjs"
if [[ ! -f "$SRC" ]]; then
  echo "✗ source bridge not found: $SRC" >&2
  echo "  set BERTOS_BRAIN_DIR=/path/to/bertosV2 and re-run." >&2
  exit 1
fi
mkdir -p "$(dirname "$DST")"
cp "$SRC" "$DST"
echo "✓ synced bridge: $SRC -> $DST"
echo "  rebuild the body image to pick it up: docker compose ... up -d --build"
