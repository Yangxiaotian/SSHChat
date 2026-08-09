#!/usr/bin/env bash
# Register a trusted peer SSHChat server for federation (larger SSH network).
#
# Mutual trust: on server A run this with B's federation pubkey; on B run with A's.
# Each side also needs the other's node in federation/peers.json (this script writes it).
#
# Usage:
#   sudo ./admin-add-peer.sh <peer_node_id> <peer_host> <peer_pubkey_line_or_file>
#   sudo ./admin-add-peer.sh server-b b.example.com ssh-ed25519 AAAA... comment
#   sudo ./admin-add-peer.sh server-b b.example.com /path/to/b-federation.pub
#
# Options (env):
#   SSHCHAT_PREFIX          Install dir (default: dir of this script)
#   SSHCHAT_FEDERATION_USER Federation SSH user (default: sshchat-federation)
#   SSHCHAT_PEER_SSH_PORT   Peer sshd port (default: 22)
#   SSHCHAT_PEER_FED_PORT   Peer federation TCP port (default: SSHCHAT_PORT+1)
#   SSHCHAT_PEER_MODE       ssh | tcp (default: ssh)

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREFIX=${SSHCHAT_PREFIX:-$SCRIPT_DIR}
FED_USER=${SSHCHAT_FEDERATION_USER:-sshchat-federation}
FED_HOME=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}
PEER_SSH_PORT=${SSHCHAT_PEER_SSH_PORT:-22}
PEER_MODE=${SSHCHAT_PEER_MODE:-ssh}
FED_DIR="$PREFIX/federation"
PEERS_JSON="$FED_DIR/peers.json"
BRIDGE="$PREFIX/federation-bridge.sh"
KEY_PRIV="$FED_DIR/id_ed25519"
KEY_PUB="$FED_DIR/id_ed25519.pub"

is_darwin() { [[ "$(uname -s)" == "Darwin" ]]; }

usage() {
  cat >&2 <<EOF
Usage: sudo $0 <peer_node_id> <peer_host> <pubkey-or-file>

Registers peer for outbound SSH federation and adds their pubkey for inbound links.
Shows this server's federation public key for copying to the remote admin.

Env: SSHCHAT_PEER_SSH_PORT, SSHCHAT_PEER_FED_PORT, SSHCHAT_PEER_MODE=(ssh|tcp)
EOF
}

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

if [[ $# -lt 3 ]]; then
  usage
  exit 1
fi

PEER_NODE_ID=$1
PEER_HOST=$2
PUBKEY_ARG=$3

if [[ -f "$PUBKEY_ARG" ]]; then
  PEER_PUBKEY=$(head -n1 "$PUBKEY_ARG" | tr -d '\r')
else
  PEER_PUBKEY=$PUBKEY_ARG
fi

if [[ ! "$PEER_PUBKEY" =~ ^(ssh-(rsa|ed25519|ecdsa|dss)[[:space:]].+)$ ]]; then
  echo "error: invalid OpenSSH public key line" >&2
  exit 1
fi

if [[ -f "$PREFIX/sshchat.env" ]]; then
  # shellcheck disable=SC1091
  . "$PREFIX/sshchat.env"
fi
CHAT_PORT=${SSHCHAT_PORT:-12345}
PEER_FED_PORT=${SSHCHAT_PEER_FED_PORT:-$((CHAT_PORT + 1))}

mkdir -p "$FED_DIR"
chmod 750 "$FED_DIR"

if [[ ! -f "$KEY_PRIV" ]]; then
  ssh-keygen -t ed25519 -f "$KEY_PRIV" -N "" -C "sshchat-federation@$(hostname -f 2>/dev/null || hostname)"
  chmod 600 "$KEY_PRIV"
  chmod 644 "$KEY_PUB"
  echo "info: generated federation key $KEY_PUB"
fi

# Ensure federation user exists (Linux). Home is separate from FED_DIR so sshd
# accepts ownership (service user owns FED_DIR for peers.json + private key).
if ! is_darwin; then
  if ! id "$FED_USER" &>/dev/null; then
    mkdir -p "$FED_HOME"
    useradd -r -s /usr/sbin/nologin -d "$FED_HOME" -c "SSHChat federation" "$FED_USER"
    echo "info: created system user $FED_USER (home $FED_HOME)"
  else
    cur_home=$(getent passwd "$FED_USER" | cut -d: -f6)
    if [[ -n "$cur_home" && "$cur_home" != "$FED_HOME" ]]; then
      mkdir -p "$FED_HOME"
      if [[ -f "$cur_home/.ssh/authorized_keys" && ! -f "$FED_HOME/.ssh/authorized_keys" ]]; then
        mkdir -p "$FED_HOME/.ssh"
        cp -a "$cur_home/.ssh/authorized_keys" "$FED_HOME/.ssh/authorized_keys"
      fi
      usermod -d "$FED_HOME" "$FED_USER" 2>/dev/null || true
      echo "info: federation SSH home -> $FED_HOME (was $cur_home)"
    fi
  fi
  mkdir -p "$FED_HOME"
  chown "$FED_USER:$FED_USER" "$FED_HOME"
  chmod 755 "$FED_HOME"
  install -d -m 700 -o "$FED_USER" -g "$FED_USER" "$FED_HOME/.ssh"
  AUTH_DIR=$(getent passwd "$FED_USER" | cut -d: -f6)
  AUTH_DIR=${AUTH_DIR:-$FED_HOME}
  AUTH_KEYS="$AUTH_DIR/.ssh/authorized_keys"
  mkdir -p "$(dirname "$AUTH_KEYS")"
  chown "$FED_USER:$FED_USER" "$(dirname "$AUTH_KEYS")"
  chmod 700 "$(dirname "$AUTH_KEYS")"
else
  AUTH_KEYS="$FED_DIR/authorized_keys_inbound"
  touch "$AUTH_KEYS"
  chmod 600 "$AUTH_KEYS"
fi

# Local federation port the peer will ssh -W into (this machine).
LOCAL_FED_PORT=${SSHCHAT_FEDERATION_PORT:-$((CHAT_PORT + 1))}
# Prefer ssh -W + permitopen over forced-command+nc: more reliable under sshd,
# and avoids no-port-forwarding rejecting the tunnel.
OPTS="restrict,port-forwarding,permitopen=\"127.0.0.1:${LOCAL_FED_PORT}\",permitopen=\"[::1]:${LOCAL_FED_PORT}\""
FINAL_LINE="$OPTS $PEER_PUBKEY"
KEY_BLOB=$(awk '{print $2}' <<<"$PEER_PUBKEY")
if [[ -f "$AUTH_KEYS" ]] && grep -qF "$KEY_BLOB" "$AUTH_KEYS" 2>/dev/null; then
  # Upgrade legacy command=/no-port-forwarding lines to permitopen.
  if grep -F "$KEY_BLOB" "$AUTH_KEYS" | grep -q 'permitopen='; then
    echo "info: peer pubkey already in $AUTH_KEYS"
  else
    TMP=$(mktemp)
    grep -vF "$KEY_BLOB" "$AUTH_KEYS" >"$TMP" || true
    echo "$FINAL_LINE" >>"$TMP"
    mv "$TMP" "$AUTH_KEYS"
    chmod 600 "$AUTH_KEYS"
    if ! is_darwin; then
      chown "$FED_USER:$FED_USER" "$AUTH_KEYS"
    fi
    echo "info: updated peer inbound key options in $AUTH_KEYS (permitopen ${LOCAL_FED_PORT})"
  fi
else
  echo "$FINAL_LINE" >>"$AUTH_KEYS"
  chmod 600 "$AUTH_KEYS"
  if ! is_darwin; then
    chown "$FED_USER:$FED_USER" "$AUTH_KEYS"
  fi
  echo "info: added peer inbound key to $AUTH_KEYS (permitopen ${LOCAL_FED_PORT})"
fi

# Merge peers.json
SSHCHAT__PEERS="$PEERS_JSON" \
SSHCHAT__NODE="$PEER_NODE_ID" \
SSHCHAT__HOST="$PEER_HOST" \
SSHCHAT__SSH_PORT="$PEER_SSH_PORT" \
SSHCHAT__FED_PORT="$PEER_FED_PORT" \
SSHCHAT__MODE="$PEER_MODE" \
SSHCHAT__KEY="$KEY_PRIV" \
SSHCHAT__PEER_PUBKEY="$PEER_PUBKEY" \
python3 - <<'PY'
import json, os, pathlib

path = pathlib.Path(os.environ["SSHCHAT__PEERS"])
path.parent.mkdir(parents=True, exist_ok=True)
peers = []
if path.is_file():
    try:
        peers = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        peers = []
if not isinstance(peers, list):
    peers = []
node = os.environ["SSHCHAT__NODE"]
entry = {
    "node_id": node,
    "host": os.environ["SSHCHAT__HOST"],
    "ssh_port": int(os.environ["SSHCHAT__SSH_PORT"]),
    "federation_port": int(os.environ["SSHCHAT__FED_PORT"]),
    "ssh_user": os.environ.get("SSHCHAT_FEDERATION_USER", "sshchat-federation"),
    "ssh_key": os.environ["SSHCHAT__KEY"],
    "mode": os.environ["SSHCHAT__MODE"],
    "peer_pubkey": os.environ.get("SSHCHAT__PEER_PUBKEY", "").strip(),
}
peers = [p for p in peers if isinstance(p, dict) and p.get("node_id") != node]
peers.append(entry)
path.write_text(json.dumps(peers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"info: updated {path}")
PY

chmod 640 "$PEERS_JSON"
# Service user (sshchat) must read peers.json; root:root 640 breaks federation reload.
SVC_USER=${SSHCHAT_RUN_USER:-}
if [[ -z "$SVC_USER" ]] && [[ -f /etc/systemd/system/sshchat.service ]]; then
  SVC_USER=$(awk -F= '/^User=/{print $2; exit}' /etc/systemd/system/sshchat.service 2>/dev/null || true)
fi
SVC_USER=${SVC_USER:-sshchat}
if id "$SVC_USER" &>/dev/null; then
  chown "$SVC_USER:$SVC_USER" "$PEERS_JSON" 2>/dev/null || true
  chown "$SVC_USER:$SVC_USER" "$FED_DIR" 2>/dev/null || true
  chmod 750 "$FED_DIR" 2>/dev/null || true
  if [[ -f "$KEY_PRIV" ]]; then
    chown "$SVC_USER:$SVC_USER" "$KEY_PRIV" "$KEY_PUB" 2>/dev/null || true
    chmod 600 "$KEY_PRIV" 2>/dev/null || true
    chmod 644 "$KEY_PUB" 2>/dev/null || true
  fi
fi
if ! is_darwin && id "$FED_USER" &>/dev/null; then
  # Need group access to traverse /opt/sshchat (750) and read sshchat.env for bridge.
  CLIENT_GROUP_NAME=${SSHCHAT_CLIENT_GROUP:-sshchat-clients}
  if getent group "$CLIENT_GROUP_NAME" >/dev/null 2>&1; then
    usermod -aG "$CLIENT_GROUP_NAME" "$FED_USER" 2>/dev/null || true
  fi
  if [[ -n "${AUTH_KEYS:-}" && -f "$AUTH_KEYS" ]]; then
    chown "$FED_USER:$FED_USER" "$(dirname "$AUTH_KEYS")" "$AUTH_KEYS" 2>/dev/null || true
    chmod 700 "$(dirname "$AUTH_KEYS")" 2>/dev/null || true
    chmod 600 "$AUTH_KEYS" 2>/dev/null || true
  fi
  # Keep sshd-compliant home ownership.
  chown "$FED_USER:$FED_USER" "$FED_HOME" 2>/dev/null || true
  chmod 755 "$FED_HOME" 2>/dev/null || true
fi
# Bridge is forced-command for inbound peers.
chmod 755 "$BRIDGE" 2>/dev/null || true

# Hot-reload running sshchat so new peers connect without a full restart.
# authorized_keys is already live for inbound; this picks up peers.json outbound.
signal_sshchat_reload() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet sshchat.service 2>/dev/null; then
    if systemctl kill -s HUP sshchat.service 2>/dev/null; then
      echo "info: sent SIGHUP to sshchat.service (reload federation peers)"
      return 0
    fi
  fi
  # Fallback: signal the python server process for this PREFIX.
  if command -v pkill >/dev/null 2>&1; then
    if pkill -HUP -f "$PREFIX/venv/bin/python $PREFIX/server.py" 2>/dev/null \
      || pkill -HUP -f "$PREFIX/server.py" 2>/dev/null; then
      echo "info: sent SIGHUP to sshchat server process (reload federation peers)"
      return 0
    fi
  fi
  echo "info: sshchat not signaled (not running?). New peers load on next start, or within a few seconds if the service is up (peers.json watch)."
}

signal_sshchat_reload

echo
echo "=== This server's federation public key (add on peer with their admin-add-peer.sh) ==="
cat "$KEY_PUB"
echo
echo "=== Local node id (set SSHCHAT_NODE_ID in sshchat.env if you want a fixed name) ==="
echo "${SSHCHAT_NODE_ID:-$(hostname)}"
echo
echo "No full restart required: peers.json was updated and sshchat was signaled to reload."
echo "Remember: the other server must also run admin-add-peer.sh for this node (mutual trust)."
