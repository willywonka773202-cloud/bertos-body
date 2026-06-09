#!/bin/bash
# OPTIONAL: package the bertosV2 "brain" code (no node_modules/.next/.git) for
# Taildrop to the always-on desktop, to update its brain to the latest routes so
# ALL 19 BertOS brain tools work (the core build/automate/council tools already
# work against an older brain). Run on the LAPTOP. The desktop then extracts it,
# runs `npm install`, and restarts `npm run bertos:host`.
#
#   bash deploy/desktop/package-brain.sh
set -euo pipefail
BRAIN="${BERTOS_BRAIN_DIR:-$HOME/Documents/bertosV2}"
OUT="${OUT:-$HOME/bertos-migration}"
DEST_NODE="${DEST_NODE:-desktop-u3m3uq1}"
mkdir -p "$OUT"
if [[ ! -d "$BRAIN" ]]; then
  echo "✗ brain repo not found: $BRAIN (set BERTOS_BRAIN_DIR)" >&2; exit 1
fi
# Exclude the heavy/rebuildable dirs; ship code + scripts + the expanded bridge.
tar -czf "$OUT/bertos-brain.tgz" \
  --exclude='node_modules' --exclude='.next' --exclude='.next-prod' \
  --exclude='.git' --exclude='*.log' \
  -C "$(dirname "$BRAIN")" "$(basename "$BRAIN")"
echo "✓ brain code → $OUT/bertos-brain.tgz  ($(du -h "$OUT/bertos-brain.tgz" | cut -f1))"
echo
echo "Send to the desktop:"
echo "  tailscale file cp \"$OUT/bertos-brain.tgz\" ${DEST_NODE}:"
echo
echo "On the desktop: extract, then  (cd bertosV2 && npm install && npm run bertos:host)"
