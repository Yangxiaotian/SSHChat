#!/usr/bin/env bash
# Add a local user and an SSH public key that forces chat.sh on login.
# Intended to run as root on Linux (useradd/getent).
#
# Usage:
#   sudo ./admin-add-user.sh <username> <path-to-pubkey>
#   cat id_ed25519.pub | sudo ./admin-add-user.sh <username> -
#
# Env (optional):
#   SSHCHAT_CHAT_SCRIPT  Absolute or relative path to chat.sh (default: next to this script)
#   SSHCHAT_SHELL        Login shell for new users (default: /usr/sbin/nologin)

set -euo pipefail

usage() {
  echo "Usage: $0 <username> <public_key_file|->" >&2
  echo "  Reads one line from the file or stdin (-). Run as root." >&2
}

[[ $# -eq 2 ]] || { usage; exit 1; }
[[ ${EUID:-0} -eq 0 ]] || { echo "error: must run as root" >&2; exit 1; }

USER_NAME=$1
KEY_SRC=$2

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
: "${SSHCHAT_CHAT_SCRIPT:=$SCRIPT_DIR/chat.sh}"
: "${SSHCHAT_SHELL:=/usr/sbin/nologin}"

if ! [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "error: invalid username (lowercase POSIX-ish, max 32 chars)" >&2
  exit 1
fi

if ! CHAT_SCRIPT=$(cd "$(dirname "$SSHCHAT_CHAT_SCRIPT")" && pwd)/$(basename "$SSHCHAT_CHAT_SCRIPT"); then
  echo "error: cannot resolve SSHCHAT_CHAT_SCRIPT directory" >&2
  exit 1
fi
if [[ ! -f "$CHAT_SCRIPT" ]]; then
  echo "error: chat script not found: $CHAT_SCRIPT" >&2
  exit 1
fi
if [[ ! -x "$CHAT_SCRIPT" ]]; then
  echo "warning: chat script is not executable: $CHAT_SCRIPT" >&2
fi

if [[ "$KEY_SRC" == "-" ]]; then
  IFS= read -r KEY_LINE || true
else
  [[ -f "$KEY_SRC" ]] || { echo "error: key file not found: $KEY_SRC" >&2; exit 1; }
  IFS= read -r KEY_LINE <"$KEY_SRC" || true
fi

KEY_LINE=${KEY_LINE//$'\r'/}
KEY_LINE=$(printf '%s' "$KEY_LINE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
[[ -n "$KEY_LINE" ]] || { echo "error: empty public key line" >&2; exit 1; }

if [[ "$KEY_LINE" == command=* ]]; then
  FINAL_LINE=$KEY_LINE
else
  OPTS="command=\"${CHAT_SCRIPT}\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding"
  if [[ "$KEY_LINE" =~ ^(ssh-(rsa|ed25519|ecdsa|dss)[[:space:]].+)$ ]]; then
    FINAL_LINE="$OPTS $KEY_LINE"
  elif [[ "$KEY_LINE" =~ ^(.*)[[:space:]](ssh-(rsa|ed25519|ecdsa|dss)[[:space:]].+)$ ]]; then
    optpart=${BASH_REMATCH[1]}
    keypart=${BASH_REMATCH[2]}
    optpart=${optpart//$'\r'/}
    optpart=$(printf '%s' "$optpart" | sed 's/[[:space:]]*$//')
    if [[ -n "$optpart" ]]; then
      FINAL_LINE="${OPTS},${optpart} ${keypart}"
    else
      FINAL_LINE="$OPTS $keypart"
    fi
  else
    echo "error: key line must start with ssh-* or include ' ssh-*' (OpenSSH format)" >&2
    exit 1
  fi
fi

if id "$USER_NAME" &>/dev/null; then
  echo "info: user exists: $USER_NAME"
else
  if ! command -v useradd &>/dev/null; then
    echo "error: useradd not found (this script targets Linux with shadow-utils)" >&2
    exit 1
  fi
  useradd -m -s "$SSHCHAT_SHELL" "$USER_NAME"
  echo "info: created user $USER_NAME (shell $SSHCHAT_SHELL)"
fi

if command -v getent &>/dev/null; then
  HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
else
  HOME_DIR=$(eval echo "~$USER_NAME")
fi
[[ -n "$HOME_DIR" && -d "$HOME_DIR" ]] || {
  echo "error: home directory missing for $USER_NAME" >&2
  exit 1
}

install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
if [[ ! -f "$AUTH_KEYS" ]]; then
  umask 077
  touch "$AUTH_KEYS"
fi
chown "$USER_NAME:$USER_NAME" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

if grep -Fxq "$FINAL_LINE" "$AUTH_KEYS"; then
  echo "info: identical authorized_keys entry already present; nothing to do"
  exit 0
fi

printf '%s\n' "$FINAL_LINE" >>"$AUTH_KEYS"
echo "info: appended key for $USER_NAME -> $AUTH_KEYS"
echo "info: forced command: $CHAT_SCRIPT"
