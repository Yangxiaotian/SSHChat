#!/usr/bin/env bash
# Add a local user and an SSH public key that forces chat.sh on login.
# Linux: useradd/getent. macOS: dscl + createhomedir (same CLI as Linux).
#
# Usage:
#   sudo ./admin-add-user.sh <username> <pasted-openssh-pubkey-line>
#   sudo ./admin-add-user.sh alice ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… alice@laptop
#   sudo ./admin-add-user.sh <username> <path-to-pubkey-file>   # if path exists, first line is used
#   cat id_ed25519.pub | sudo ./admin-add-user.sh <username> -
#
# Env (optional):
#   SSHCHAT_CHAT_SCRIPT  Absolute or relative path to chat.sh (default: next to this script)
#   SSHCHAT_SHELL        Login shell for new users (default: /bin/sh; macOS new users too)
#   SSHCHAT_CLIENT_GROUP  Group with read/execute on chat.sh and venv (default: sshchat-clients)

set -euo pipefail

: "${SSHCHAT_CLIENT_GROUP:=sshchat-clients}"

is_darwin() {
  [[ "$(uname -s)" == "Darwin" ]]
}

darwin_staff_gid() {
  local g
  g=$(dscl . -read /Groups/staff PrimaryGroupID 2>/dev/null | awk '{print $2}' || true)
  if [[ -n "$g" && "$g" =~ ^[0-9]+$ ]]; then
    printf '%s' "$g"
    return
  fi
  printf '%s' "20"
}

darwin_next_uid() {
  local max=500 uid _name
  while read -r _name uid; do
    [[ "$uid" =~ ^[0-9]+$ ]] || continue
    ((uid > max)) && max=$uid
  done < <(dscl . -list /Users UniqueID 2>/dev/null || true)
  echo $((max + 1))
}

create_user_darwin() {
  local name=$1 home="/Users/$1" uid staff_gid pw
  if [[ -e "$home" && ! -d "$home" ]]; then
    echo "error: macOS: $home exists and is not a directory" >&2
    exit 1
  fi
  if ! command -v dscl &>/dev/null; then
    echo "error: macOS: dscl not found (cannot create user)" >&2
    exit 1
  fi
  uid=$(darwin_next_uid)
  staff_gid=$(darwin_staff_gid)
  pw=$(openssl rand -base64 32 | tr -d '\n')

  dscl . -create "$home"
  dscl . -create "$home" UserShell "$SSHCHAT_SHELL"
  dscl . -create "$home" RealName "$name"
  dscl . -create "$home" UniqueID "$uid"
  dscl . -create "$home" PrimaryGroupID "$staff_gid"
  dscl . -create "$home" NFSHomeDirectory "$home"
  dscl . -passwd "$home" "$pw"

  if command -v createhomedir &>/dev/null; then
    createhomedir -c -u "$name" 2>/dev/null || true
  fi
  if [[ ! -d "$home" ]]; then
    mkdir -p "$home"
    # Primary group is often "staff", not a same-named group (matches .ssh install below).
    chown "${name}:$(id -gn "$name")" "$home"
    chmod 751 "$home"
  fi

  if ! id "$name" &>/dev/null; then
    echo "error: macOS: user record created but id(1) does not see $name" >&2
    exit 1
  fi
  echo "info: created macOS user $name (uid $uid, home $home, shell $SSHCHAT_SHELL)"
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

  if getent group "$SSHCHAT_CLIENT_GROUP" &>/dev/null 2>&1; then
    return 0
  fi
  
  # Try grep /etc/group as fallback if getent is not available (e.g., minimal ish/Alpine)
  if ! command -v getent &>/dev/null && grep -q "^${SSHCHAT_CLIENT_GROUP}:" /etc/group 2>/dev/null; then
    return 0
  fi
  
  # Prefer groupadd (standard Linux), fall back to addgroup (Alpine/BusyBox)
  if command -v groupadd &>/dev/null; then
    groupadd -r "$SSHCHAT_CLIENT_GROUP"
    echo "info: created system group $SSHCHAT_CLIENT_GROUP"
  elif command -v addgroup &>/dev/null; then
    addgroup -S "$SSHCHAT_CLIENT_GROUP" 2>/dev/null || addgroup "$SSHCHAT_CLIENT_GROUP"
    echo "info: created system group $SSHCHAT_CLIENT_GROUP"
  else
    echo "error: group $SSHCHAT_CLIENT_GROUP missing and neither groupadd nor addgroup found (run deploy.sh first?)" >&2
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
    create_user_darwin "$USER_NAME"
  else
    # Prefer useradd (standard Linux), fall back to adduser (Alpine/BusyBox)
    if command -v useradd &>/dev/null; then
      useradd -m -s "$SSHCHAT_SHELL" "$USER_NAME"
      echo "info: created user $USER_NAME (shell $SSHCHAT_SHELL)"
    elif command -v adduser &>/dev/null; then
      # Alpine/BusyBox adduser: -D no password, -s shell, -h creates home
      adduser -D -s "$SSHCHAT_SHELL" "$USER_NAME"
      echo "info: created user $USER_NAME (shell $SSHCHAT_SHELL)"
    else
      echo "error: neither useradd nor adduser found (this script targets Linux with user management tools)" >&2
      exit 1
    fi
  fi
fi

# Alpine adduser -D leaves shadow '!' (locked); OpenSSH then rejects publickey too.
# '*' disables password login but allows pubkey. Also fix pre-existing locked users.
if ! is_darwin && [[ -f /etc/shadow ]] && grep -q "^${USER_NAME}:!" /etc/shadow; then
  if echo "${USER_NAME}:*" | chpasswd -e 2>/dev/null; then
    echo "info: unlocked $USER_NAME for pubkey auth (shadow '!' -> '*')"
  else
    echo "warning: could not unlock $USER_NAME in /etc/shadow; pubkey login may fail" >&2
  fi
fi

if ! is_darwin; then
  ensure_client_group
  if ! id -nG "$USER_NAME" | grep -qw "$SSHCHAT_CLIENT_GROUP"; then
    # Prefer usermod (standard Linux), fall back to addgroup (Alpine/BusyBox)
    if command -v usermod &>/dev/null; then
      usermod -aG "$SSHCHAT_CLIENT_GROUP" "$USER_NAME"
      echo "info: added $USER_NAME to group $SSHCHAT_CLIENT_GROUP"
    elif command -v addgroup &>/dev/null; then
      # Alpine/BusyBox: addgroup USER GROUP (adds user to existing group)
      addgroup "$USER_NAME" "$SSHCHAT_CLIENT_GROUP"
      echo "info: added $USER_NAME to group $SSHCHAT_CLIENT_GROUP"
    else
      echo "error: neither usermod nor addgroup found" >&2
      exit 1
    fi
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

# macOS default primary group is usually "staff", not a private group named like the user.
USER_GROUP=$(id -gn "$USER_NAME")

install -d -m 700 -o "$USER_NAME" -g "$USER_GROUP" "$HOME_DIR/.ssh"
AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
if [[ ! -f "$AUTH_KEYS" ]]; then
  umask 077
  touch "$AUTH_KEYS"
fi
chown "$USER_NAME:$USER_GROUP" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

if grep -Fxq "$FINAL_LINE" "$AUTH_KEYS"; then
  echo "info: identical authorized_keys entry already present; nothing to do"
  exit 0
fi

printf '%s\n' "$FINAL_LINE" >>"$AUTH_KEYS"
echo "info: appended key for $USER_NAME -> $AUTH_KEYS"
echo "info: forced command: $CHAT_SCRIPT"
