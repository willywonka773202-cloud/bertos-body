#!/bin/bash
# Package this machine's live BertOS state (data dir + memory vault) into two
# tarballs and print the Taildrop command to send them to the always-on
# desktop host. Run on the LAPTOP (the machine BertOS is currently configured
# on), from anywhere — paths are resolved relative to this script.
#
#   bash deploy/desktop/package-migration.sh
#
# What travels:
#   data/            -> bertos-data.tgz   (incl. .app_key Fernet key, app.db,
#                                          user_prefs.json, settings.json)
#   <memory vault>   -> bertos-vault.tgz  (the app-owned Memory/bertos/ + your
#                                          human notes — the whole vault)
# The Fernet key MUST travel or the migrated Gmail/CalDAV passwords won't
# decrypt on the desktop. Taildrop runs over WireGuard between your own devices,
# so the key never leaves your tailnet.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT:-$HOME/bertos-migration}"
DEST_NODE="${DEST_NODE:-desktop-u3m3uq1}"   # the desktop's Tailscale name
mkdir -p "$OUT"

echo "Repo:      $REPO"
echo "Output:    $OUT"
echo "Dest node: $DEST_NODE"
echo

# 1. Consistent snapshot: stop the laptop server first. Two live instances must
#    not both poll email / fire the daily brief (you'd get double phone pushes).
if pgrep -f "uvicorn app:app" >/dev/null 2>&1; then
  echo "⚠  A local BertOS server (uvicorn app:app) is running."
  echo "   Stop it for a clean DB snapshot — the desktop becomes the primary."
  read -r -p "   Stop it now and run:  pkill -f 'uvicorn app:app'  ? [y/N] " ans
  if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
    pkill -f "uvicorn app:app" || true
    sleep 2
    echo "   stopped."
  else
    echo "   Leaving it running — the DB snapshot may be slightly inconsistent."
  fi
  echo
fi

# 2. data dir (dotfiles like .app_key are included automatically).
if [[ ! -d "$REPO/data" ]]; then
  echo "✗ $REPO/data not found — is this the configured BertOS machine?" >&2
  exit 1
fi
tar -czf "$OUT/bertos-data.tgz" -C "$REPO" data
echo "✓ data  → $OUT/bertos-data.tgz   ($(du -h "$OUT/bertos-data.tgz" | cut -f1))"

# 3. memory vault (BERTOS_OBSIDIAN_VAULT env — what the app reads — else the
#    documented default).
VAULT="${BERTOS_OBSIDIAN_VAULT:-$HOME/Documents/BertOS-Vault}"
if [[ ! -d "$VAULT" ]]; then
  echo "✗ vault not found at: $VAULT" >&2
  echo "  set BERTOS_OBSIDIAN_VAULT=/path/to/vault and re-run." >&2
  exit 1
fi
tar -czf "$OUT/bertos-vault.tgz" -C "$(dirname "$VAULT")" "$(basename "$VAULT")"
echo "✓ vault → $OUT/bertos-vault.tgz  ($(du -h "$OUT/bertos-vault.tgz" | cut -f1))"

# 4. Send over Tailscale (Taildrop — no SSH needed; the desktop has SSH off).
echo
echo "Send both to the desktop (run this now):"
echo
echo "  tailscale file cp \"$OUT/bertos-data.tgz\" \"$OUT/bertos-vault.tgz\" ${DEST_NODE}:"
echo
echo "Then continue on the desktop with deploy/desktop/DEPLOY-DESKTOP.md (Part B)."
