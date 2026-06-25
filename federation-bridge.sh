#!/usr/bin/env bash
# Forced-command entry for inbound federation SSH: bridge stdio to local federation port.
set -euo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$DIR/sshchat.env" ]]; then
  # shellcheck disable=SC1091
  . "$DIR/sshchat.env"
fi

CHAT_PORT=${SSHCHAT_PORT:-12345}
FED_PORT=${SSHCHAT_FEDERATION_PORT:-$((CHAT_PORT + 1))}

if command -v nc &>/dev/null; then
  exec nc 127.0.0.1 "$FED_PORT"
fi
if command -v ncat &>/dev/null; then
  exec ncat 127.0.0.1 "$FED_PORT"
fi

echo "error: nc/ncat not found for federation bridge" >&2
exit 1
