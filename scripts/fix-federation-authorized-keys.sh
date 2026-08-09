#!/usr/bin/env bash
# Rewrite federation authorized_keys from legacy forced-command / no-port-forwarding
# to restrict+permitopen so ssh -W into the local federation port works.
#
#   sudo ./scripts/fix-federation-authorized-keys.sh
set -euo pipefail

PREFIX=${SSHCHAT_PREFIX:-/opt/sshchat}
FED_USER=${SSHCHAT_FEDERATION_USER:-sshchat-federation}
FED_HOME=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

if [[ -f "$PREFIX/sshchat.env" ]]; then
  # shellcheck disable=SC1091
  . "$PREFIX/sshchat.env"
fi
CHAT_PORT=${SSHCHAT_PORT:-12345}
LOCAL_FED_PORT=${SSHCHAT_FEDERATION_PORT:-$((CHAT_PORT + 1))}

if [[ "$(uname -s)" == "Darwin" ]]; then
  AUTH_KEYS="$PREFIX/federation/authorized_keys_inbound"
else
  AUTH_DIR=$(getent passwd "$FED_USER" | cut -d: -f6)
  AUTH_DIR=${AUTH_DIR:-$FED_HOME}
  AUTH_KEYS="$AUTH_DIR/.ssh/authorized_keys"
fi

[[ -f "$AUTH_KEYS" ]] || { echo "error: missing $AUTH_KEYS" >&2; exit 1; }

OPTS="restrict,port-forwarding,permitopen=\"127.0.0.1:${LOCAL_FED_PORT}\",permitopen=\"[::1]:${LOCAL_FED_PORT}\""
TMP=$(mktemp)
changed=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^# ]] && { echo "$line" >>"$TMP"; continue; }
  # OpenSSH pubkey fields: options? key-type key-blob comment...
  if [[ "$line" =~ (ssh-(ed25519|rsa|ecdsa|dss)|ecdsa-sha2-[^[:space:]]+)[[:space:]]+([^[:space:]]+) ]]; then
    keytype="${BASH_REMATCH[1]}"
    blob="${BASH_REMATCH[3]}"
    # Preserve trailing comment if present after blob.
    rest=${line#*"$blob"}
    rest=${rest##+([[:space:]])}
    if [[ "$line" == *"permitopen="* ]]; then
      echo "$line" >>"$TMP"
    else
      if [[ -n "$rest" ]]; then
        echo "$OPTS $keytype $blob $rest" >>"$TMP"
      else
        echo "$OPTS $keytype $blob" >>"$TMP"
      fi
      changed=1
      echo "info: rewrote key …${blob: -12}"
    fi
  else
    echo "$line" >>"$TMP"
  fi
done <"$AUTH_KEYS"

mv "$TMP" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
if id "$FED_USER" &>/dev/null; then
  chown "$FED_USER:$FED_USER" "$AUTH_KEYS" 2>/dev/null || true
fi

if [[ "$changed" -eq 1 ]]; then
  echo "Updated $AUTH_KEYS for permitopen 127.0.0.1:${LOCAL_FED_PORT}"
else
  echo "No legacy lines found in $AUTH_KEYS (already permitopen?)"
fi
echo "Restart peer sshchat (or wait for reconnect). Watch: journalctl -t sshd -f"
