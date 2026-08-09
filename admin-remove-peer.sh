#!/usr/bin/env bash
# Remove a trusted peer SSHChat server from federation.
#
# Drops the peer from peers.json and (when known) their inbound pubkey from
# authorized_keys, then SIGHUP-reloads sshchat so the live link closes and
# users get a nodedown notice. Do the same on the other server for a clean cut.
#
# Usage:
#   sudo ./admin-remove-peer.sh <peer_node_id>
#   sudo ./admin-remove-peer.sh <peer_node_id> <peer_pubkey_line_or_file>
#
# If peers.json has peer_pubkey (written by admin-add-peer.sh), the optional
# second argument is not required. Otherwise pass the pubkey to scrub
# authorized_keys, or clean that file manually.
#
# Options (env):
#   SSHCHAT_PREFIX          Install dir (default: dir of this script)
#   SSHCHAT_FEDERATION_USER Federation SSH user (default: sshchat-federation)

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREFIX=${SSHCHAT_PREFIX:-$SCRIPT_DIR}
FED_USER=${SSHCHAT_FEDERATION_USER:-sshchat-federation}
FED_HOME=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}
FED_DIR="$PREFIX/federation"
PEERS_JSON="$FED_DIR/peers.json"

is_darwin() { [[ "$(uname -s)" == "Darwin" ]]; }

usage() {
  cat >&2 <<EOF
Usage: sudo $0 <peer_node_id> [pubkey-or-file]

Removes peer from peers.json and inbound authorized_keys, then hot-reloads
sshchat (SIGHUP) so the link drops and local users are notified.
EOF
}

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

PEER_NODE_ID=$1
PUBKEY_ARG=${2:-}

if [[ -f "$PREFIX/sshchat.env" ]]; then
  # shellcheck disable=SC1091
  . "$PREFIX/sshchat.env"
fi

if [[ ! -f "$PEERS_JSON" ]]; then
  echo "error: no peers file at $PEERS_JSON" >&2
  exit 1
fi

# Resolve authorized_keys path.
if ! is_darwin; then
  if id "$FED_USER" &>/dev/null; then
    AUTH_DIR=$(getent passwd "$FED_USER" | cut -d: -f6)
    AUTH_KEYS="$AUTH_DIR/.ssh/authorized_keys"
  else
    AUTH_KEYS=""
  fi
else
  AUTH_KEYS="$FED_DIR/authorized_keys_inbound"
fi

PEER_PUBKEY=""
if [[ -n "$PUBKEY_ARG" ]]; then
  if [[ -f "$PUBKEY_ARG" ]]; then
    PEER_PUBKEY=$(head -n1 "$PUBKEY_ARG" | tr -d '\r')
  else
    PEER_PUBKEY=$PUBKEY_ARG
  fi
  if [[ ! "$PEER_PUBKEY" =~ ^(ssh-(rsa|ed25519|ecdsa|dss)[[:space:]].+)$ ]]; then
    echo "error: invalid OpenSSH public key line" >&2
    exit 1
  fi
fi

# Remove from peers.json; capture stored peer_pubkey if present.
STORED_PUBKEY=$(
SSHCHAT__PEERS="$PEERS_JSON" \
SSHCHAT__NODE="$PEER_NODE_ID" \
python3 - <<'PY'
import json, os, pathlib, sys

path = pathlib.Path(os.environ["SSHCHAT__PEERS"])
node = os.environ["SSHCHAT__NODE"]
try:
    peers = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    peers = []
if not isinstance(peers, list):
    peers = []
stored = ""
kept = []
found = False
for p in peers:
    if isinstance(p, dict) and p.get("node_id") == node:
        found = True
        stored = str(p.get("peer_pubkey") or "").strip()
        continue
    kept.append(p)
if not found:
    print(f"error: peer {node!r} not in {path}", file=sys.stderr)
    sys.exit(2)
path.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(stored)
PY
) || {
  echo "error: failed to update $PEERS_JSON" >&2
  exit 1
}

echo "info: removed $PEER_NODE_ID from $PEERS_JSON"

chmod 640 "$PEERS_JSON" 2>/dev/null || true
SVC_USER=${SSHCHAT_RUN_USER:-}
if [[ -z "$SVC_USER" ]] && [[ -f /etc/systemd/system/sshchat.service ]]; then
  SVC_USER=$(awk -F= '/^User=/{print $2; exit}' /etc/systemd/system/sshchat.service 2>/dev/null || true)
fi
SVC_USER=${SVC_USER:-sshchat}
if id "$SVC_USER" &>/dev/null; then
  chown "$SVC_USER:$SVC_USER" "$PEERS_JSON" 2>/dev/null || true
fi

if [[ -z "$PEER_PUBKEY" && -n "$STORED_PUBKEY" ]]; then
  PEER_PUBKEY=$STORED_PUBKEY
fi

# Drop matching inbound key line(s).
if [[ -n "$PEER_PUBKEY" && -n "${AUTH_KEYS:-}" && -f "$AUTH_KEYS" ]]; then
  # Match on key blob (field 2) so command= options do not block removal.
  KEY_BLOB=$(awk '{print $2}' <<<"$PEER_PUBKEY")
  if [[ -n "$KEY_BLOB" ]] && grep -qF "$KEY_BLOB" "$AUTH_KEYS" 2>/dev/null; then
    TMP=$(mktemp)
    grep -vF "$KEY_BLOB" "$AUTH_KEYS" >"$TMP" || true
    mv "$TMP" "$AUTH_KEYS"
    chmod 600 "$AUTH_KEYS"
    if ! is_darwin && id "$FED_USER" &>/dev/null; then
      chown "$FED_USER:$FED_USER" "$AUTH_KEYS"
    fi
    echo "info: removed peer inbound key from $AUTH_KEYS"
  else
    echo "info: peer pubkey not found in $AUTH_KEYS (already gone?)"
  fi
elif [[ -z "$PEER_PUBKEY" ]]; then
  echo "warning: no peer_pubkey stored and none given; check $AUTH_KEYS manually if inbound trust remains."
fi

signal_sshchat_reload() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet sshchat.service 2>/dev/null; then
    if systemctl kill -s HUP sshchat.service 2>/dev/null; then
      echo "info: sent SIGHUP to sshchat.service (drop federation peer + notify)"
      return 0
    fi
  fi
  if command -v pkill >/dev/null 2>&1; then
    if pkill -HUP -f "$PREFIX/venv/bin/python $PREFIX/server.py" 2>/dev/null \
      || pkill -HUP -f "$PREFIX/server.py" 2>/dev/null; then
      echo "info: sent SIGHUP to sshchat server process (drop federation peer + notify)"
      return 0
    fi
  fi
  echo "info: sshchat not signaled (not running?). Removal applies on next start, or within a few seconds if the service is up (peers.json watch)."
}

signal_sshchat_reload

echo
echo "Peer $PEER_NODE_ID removed locally. Live link (if any) is closed and users are notified."
echo "Remember: run admin-remove-peer.sh on the other server too for a mutual clean break."
