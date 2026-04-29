#!/usr/bin/env bash
# Add a local user and an SSH public key that forces chat.sh on login.
# Intended for Linux (useradd/getent) or macOS (existing users only; see below).
#
# Usage:
#   sudo ./admin-add-user.sh <username> <pasted-openssh-pubkey-line>
#   sudo ./admin-add-user.sh alice ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… alice@laptop
#   sudo ./admin-add-user.sh <username> <path-to-pubkey-file>   # if path exists, first line is used
#   cat id_ed25519.pub | sudo ./admin-add-user.sh <username> -
#
# Env (optional):
#   SSHCHAT_CHAT_SCRIPT  Absolute or relative path to chat.sh (default: next to this script)
#   SSHCHAT_SHELL        Login shell for new users (default: /bin/sh)
#   SSHCHAT_CLIENT_GROUP  Group with read/execute on chat.sh and venv (default: sshchat-clients)

set -euo pipefail

: "${SSHCHAT_CLIENT_GROUP:=sshchat-clients}"

is_darwin() {
  [[ "$(uname -s)" == "Darwin" ]]
}

ensure_client_group() {
  if is_darwin; then
    if dscl . -read "/Groups/$SSHCHAT_CLIENT_GROUP" &>/dev/null; then
      return 0
    fi
    if ! command -v dseditgroup &>/dev/null; then
      echo "error: dseditgroup not found (cannot create $SSHCHAT_CLIENT_GROUP)" >&2
      exit 1
    fi
    dseditgroup -o create "$SSHCHAT_CLIENT_GROUP"
    echo "info: created macOS group $SSHCHAT_CLIENT_GROUP"
    return 0
  fi

  if getent group "$SSHCHAT_CLIENT_GROUP" &>/dev/null; then
    return 0
  fi
  if command -v groupadd &>/dev/null; then
    groupadd -r "$SSHCHAT_CLIENT_GROUP"
    echo "info: created system group $SSHCHAT_CLIENT_GROUP"
  else
    echo "error: group $SSHCHAT_CLIENT_GROUP missing and groupadd not found (run deploy.sh first?)" >&2
    exit 1
  fi
}

usage() {
  echo "Usage: $0 <username> <public_key_or_file|->" >&2
  echo "  public key: paste the full ssh-ed25519 / ssh-rsa line (multiple argv words are joined with spaces)" >&2
  echo "  file:       if <public_key_or_file> is an existing file, its first line is used" >&2
  echo "  -:          read one line from stdin" >&2
  echo "Run as root." >&2
}

[[ $# -ge 2 ]] || { usage; exit 1; }
[[ ${EUID:-0} -eq 0 ]] || { echo "error: must run as root" >&2; exit 1; }

USER_NAME=$1
shift

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
: "${SSHCHAT_CHAT_SCRIPT:=$SCRIPT_DIR/chat.sh}"
: "${SSHCHAT_SHELL:=/bin/sh}"

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

if [[ $# -eq 1 && "$1" == "-" ]]; then
  IFS= read -r KEY_LINE || true
elif [[ $# -eq 1 && -f "$1" ]]; then
  IFS= read -r KEY_LINE <"$1" || true
else
  KEY_LINE="$*"
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
  if is_darwin; then
    echo "error: macOS: create \"$USER_NAME\" first (System Settings → Users & Groups), then re-run" >&2
    exit 1
  fi
  if ! command -v useradd &>/dev/null; then
    echo "error: useradd not found (this script targets Linux with shadow-utils)" >&2
    exit 1
  fi
  useradd -m -s "$SSHCHAT_SHELL" "$USER_NAME"
  echo "info: created user $USER_NAME (shell $SSHCHAT_SHELL)"
fi

if ! is_darwin; then
  ensure_client_group
  if ! command -v usermod &>/dev/null; then
    echo "error: usermod not found" >&2
    exit 1
  fi
  if ! id -nG "$USER_NAME" | grep -qw "$SSHCHAT_CLIENT_GROUP"; then
    usermod -aG "$SSHCHAT_CLIENT_GROUP" "$USER_NAME"
    echo "info: added $USER_NAME to group $SSHCHAT_CLIENT_GROUP"
  fi
else
  ensure_client_group
  if ! command -v dseditgroup &>/dev/null; then
    echo "error: dseditgroup not found" >&2
    exit 1
  fi
  if id -Gn "$USER_NAME" | tr ' ' '\n' | grep -qx "$SSHCHAT_CLIENT_GROUP"; then
    echo "info: user already in group $SSHCHAT_CLIENT_GROUP"
  else
    dseditgroup -o edit -a "$USER_NAME" -t user "$SSHCHAT_CLIENT_GROUP"
    echo "info: added $USER_NAME to group $SSHCHAT_CLIENT_GROUP (re-login required)"
  fi
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
