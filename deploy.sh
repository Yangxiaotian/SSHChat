#!/usr/bin/env bash
# One-shot install: copy app under PREFIX, venv + prompt_toolkit, sshchat.env, systemd unit.
# Linux: systemd + service user. macOS: auto local-dev (no useradd/groupadd/systemd).
# iSH (iOS Alpine): auto OpenRC + no Cloudflare + lightweight deps (no pymupdf).
# File transfer defaults to Cloudflare Quick Tunnel (override with --no-cloudflare / --file-domain).
#
#   sudo ./deploy.sh
#   sudo ./deploy.sh --prefix /Shared --server-ip 10.0.0.5 --port 12345
#   sudo ./deploy.sh --no-systemd
#   sudo ./deploy.sh --prefix /opt/sshchat --keep-env   # upgrade: keep sshchat.env
# Rewrites user authorized_keys command= to PREFIX/chat.sh each run unless --no-migrate-keys (needs perl).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Numeric uid:gid — macOS has no group named "root" (gid 0 is "wheel").
ROOT_OWN=0:0

PREFIX=/opt/sshchat
SERVER_IP=""
PORT=12345
CLIENT_SSH_HOST="${SSHCHAT_CLIENT_SSH_HOST:-}"
CLIENT_SSH_PORT="${SSHCHAT_CLIENT_SSH_PORT:-22}"
BUILD_GUI_PACKAGES=0
INSTALL_SYSTEMD=1
INSTALL_OPENRC=1
RUN_USER=sshchat
CREATE_RUN_USER=1
KEEP_ENV=0
MIGRATE_KEYS=1
RESET_ALL_RATINGS=0
RESET_GAME_RATINGS=""
RESET_USER_RATING_USER=""
RESET_USER_RATING_GAME=""
# Mirror / network knobs for pip. Default index left empty (pip uses pypi).
# sudo strips env by default — also exposed as --pip-index-url.
PIP_INDEX_URL_ARG="${SSHCHAT_PIP_INDEX_URL:-${PIP_INDEX_URL:-}}"
PIP_TIMEOUT="${SSHCHAT_PIP_TIMEOUT:-120}"
PIP_RETRIES="${SSHCHAT_PIP_RETRIES:-5}"
PIP_CMD_RETRIES="${SSHCHAT_PIP_CMD_RETRIES:-3}"
: "${SSHCHAT_CLIENT_GROUP:=sshchat-clients}"
CLIENT_GROUP=$SSHCHAT_CLIENT_GROUP
# File transfer configuration
FILE_TRANSFER_ENABLED="${SSHCHAT_FILE_TRANSFER_ENABLED:-1}"
FILE_HTTP_PORT="${SSHCHAT_FILE_HTTP_PORT:-8443}"
FILE_DOMAIN="${SSHCHAT_FILE_DOMAIN:-}"
FILE_USE_HTTPS="${SSHCHAT_FILE_USE_HTTPS:-1}"
# Cloudflare Quick Tunnel for /sendfile public URLs (default on; skip with --no-cloudflare
# or when --file-domain is set for Let's Encrypt).
USE_CLOUDFLARE="${SSHCHAT_USE_CLOUDFLARE:-1}"
FILE_STORAGE_DIR="${SSHCHAT_FILE_STORAGE_DIR:-/var/lib/sshchat/files}"
# Track whether the operator explicitly set Cloudflare so iSH defaults can yield.
CLOUDFLARE_EXPLICIT=0

is_darwin() {
  [[ "$(uname -s)" == "Darwin" ]]
}

# iSH ships Alpine under an x86 userspace emulator; marker dir is /ish.
is_ish() {
  [[ -d /ish ]] || grep -q 'apk\.ish\.app' /etc/apk/repositories 2>/dev/null
}

# Alpine BusyBox adduser -S often puts system users in "nogroup", so user:user chown fails.
primary_group_of() {
  local user_name=$1
  id -gn "$user_name" 2>/dev/null || printf '%s' "$user_name"
}

# Stop any previously running server bound to this PREFIX so the restart below
# actually picks up the freshly copied server.py / games.py / client.py rather
# than leaving an older interpreter holding the chat port.
stop_existing_server() {
  local prefix="$1"

  if command -v systemctl &>/dev/null && systemctl list-unit-files 2>/dev/null | grep -q '^sshchat\.service'; then
    if systemctl is-active --quiet sshchat.service; then
      echo "info: stopping running sshchat.service before file swap"
      systemctl stop sshchat.service || true
    fi
  fi

  if command -v rc-service &>/dev/null && [[ -f /etc/init.d/sshchat ]]; then
    if rc-service sshchat status &>/dev/null; then
      echo "info: stopping OpenRC sshchat before file swap"
      rc-service sshchat stop || true
    fi
  fi

  if command -v pgrep &>/dev/null && pgrep -f "$prefix/server.py" >/dev/null 2>&1; then
    echo "info: terminating existing server process(es) for $prefix/server.py"
    pkill -f "$prefix/server.py" || true
    # Give the kernel a moment to release the listening socket.
    local i
    for i in 1 2 3 4 5; do
      pgrep -f "$prefix/server.py" >/dev/null 2>&1 || break
      sleep 1
    done
    if pgrep -f "$prefix/server.py" >/dev/null 2>&1; then
      echo "warning: server still alive after SIGTERM; forcing"
      pkill -9 -f "$prefix/server.py" || true
      sleep 1
    fi
  fi
}

# Shell-level retry: pip's --retries does not always recover from IncompleteRead on large wheels.
pip_run_with_retry() {
  local attempt=1
  local delay=5
  while [[ "$attempt" -le "$PIP_CMD_RETRIES" ]]; do
    if "$@"; then
      return 0
    fi
    if [[ "$attempt" -ge "$PIP_CMD_RETRIES" ]]; then
      echo "error: pip failed after ${PIP_CMD_RETRIES} attempt(s): $*" >&2
      echo "hint: retry with --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple" >&2
      echo "      or SSHCHAT_PIP_TIMEOUT=300 SSHCHAT_PIP_CMD_RETRIES=5 sudo -E ./deploy.sh ..." >&2
      return 1
    fi
    echo "warn: pip attempt ${attempt}/${PIP_CMD_RETRIES} failed (${*:0:80}...); retrying in ${delay}s..." >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

usage() {
  cat >&2 <<EOF
Usage: sudo $0 [options]

Options:
  --prefix DIR       Install directory (default: $PREFIX)
  --server-ip ADDR   SSHCHAT_SERVER in sshchat.env: where server-side client.py connects for chat TCP (often 127.0.0.1; NOT the user's ssh hostname)
  --port N           Chat TCP port for server.py / SSHCHAT_PORT (default: $PORT; NOT sshd)
  --keep-env         If $PREFIX/sshchat.env already exists, do not overwrite it (upgrade-friendly)
  --no-migrate-keys  Do not rewrite authorized_keys command= paths to this install's chat.sh
  --no-systemd       Do not install or start systemd service
  --no-openrc        Do not install or start OpenRC service (iSH/Alpine)
  --run-user NAME    User to run the server as (default: $RUN_USER)
  --no-run-user      Do not create user; install as root (manual server only)
  --client-ssh-host HOST  Hostname/IP for end-user ssh / GUI installers (default: --server-ip if not loopback, else auto-detect)
  --file-domain DOMAIN    Domain for file HTTPS via Let's Encrypt (disables Cloudflare tunnel)
  --file-port N      Local file HTTP port (default: $FILE_HTTP_PORT; behind Cloudflare this stays private)
  --no-file-https    Disable HTTPS for file transfers (use HTTP only; implied by Cloudflare mode)
  --no-file-transfer Disable file transfer feature entirely
  --cloudflare       Force-enable Cloudflare Quick Tunnel for /sendfile (default: on)
  --no-cloudflare    Do not install/start Cloudflare tunnel (self-signed or --file-domain instead)
  --client-ssh-port PORT  sshd port embedded in client-bundle.json (default: $CLIENT_SSH_PORT)
  --build-gui-packages    After install, run scripts/build-gui-packages.sh if present (needs tkinter + PyInstaller)
  --reset-all-ratings     Reset all persisted chess/gomoku/xiangqi ratings before restart
  --reset-game-ratings GAME
                         Reset one game's persisted ratings before restart
  --reset-user-game-rating USER GAME
                         Reset one user's persisted rating for one game before restart
  --pip-index-url URL  Override pip index (e.g. https://pypi.tuna.tsinghua.edu.cn/simple); also reads
                       \$SSHCHAT_PIP_INDEX_URL / \$PIP_INDEX_URL (sudo strips env unless -E)
  Env: SSHCHAT_SKIP_PIP_UPGRADE=1  Skip upgrading pip before requirements (not recommended; old pip
       may fail hash checks on republished wheels).
  Env: SSHCHAT_PIP_CMD_RETRIES=N  Shell-level pip retries on network errors (default: 3)
  -h, --help         This help

Each run updates files under PREFIX and, unless --no-migrate-keys, rewrites every
scanned authorized_keys so command="…/<basename>" points at PREFIX/chat.sh.
Override matched basename with env SSHCHAT_COMMAND_BASENAME if needed. Needs perl.

Always stops any running chat server for this PREFIX (systemd sshchat.service
and/or stray python $PREFIX/server.py) before starting again, so server.py and
games.py updates always take effect without a manual restart.
EOF
}

# Stop chat TCP server so redeploy loads fresh server.py / games.py. Active room
# games and reconnect sessions are persisted to game_sessions.json and restored
# on startup when possible.
sshchat_stop_running_server() {
  local unit="/etc/systemd/system/sshchat.service"
  if command -v systemctl &>/dev/null && [[ -f "$unit" ]]; then
    if systemctl is-active --quiet sshchat.service 2>/dev/null; then
      echo "info: stopping sshchat.service (pick up new server.py / games.py)"
      systemctl stop sshchat.service || true
    fi
  fi
  if command -v rc-service &>/dev/null && [[ -f /etc/init.d/sshchat ]]; then
    if rc-service sshchat status &>/dev/null; then
      echo "info: stopping OpenRC sshchat (pick up new server.py / games.py)"
      rc-service sshchat stop || true
    fi
  fi
  local i
  for ((i = 0; i < 20; i++)); do
    if ! pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
      break
    fi
    if [[ $i -eq 0 ]]; then
      echo "info: stopping process(es) matching $PREFIX/server.py"
    fi
    pkill -f "$PREFIX/server.py" || true
    sleep 0.25
  done
  if pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
    echo "warning: $PREFIX/server.py still running after stop attempts; deploy may not load new code" >&2
  fi
}

detect_ip() {
  local ip=""
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if command -v ipconfig &>/dev/null; then
      local iface
      for iface in en0 en1 en2 en3; do
        ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
        [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && break
      done
    fi
  else
    if command -v ip &>/dev/null; then
      ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}' || true)
    fi
    if [[ -z "$ip" ]] && command -v hostname &>/dev/null; then
      ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    fi
  fi
  [[ -n "$ip" ]] || ip="127.0.0.1"
  printf '%s' "$ip"
}

ensure_client_group() {
  if is_darwin; then
    if dscl . -read "/Groups/$CLIENT_GROUP" &>/dev/null; then
      return 0
    fi
    if ! command -v dseditgroup &>/dev/null; then
      echo "error: dseditgroup not found (cannot create $CLIENT_GROUP)" >&2
      exit 1
    fi
    dseditgroup -o create "$CLIENT_GROUP"
    echo "info: created macOS group $CLIENT_GROUP (chat SSH users join via admin-add-user.sh)"
    return 0
  fi

  if getent group "$CLIENT_GROUP" &>/dev/null 2>&1; then
    return 0
  fi
  
  # Try grep /etc/group as fallback if getent is not available (e.g., minimal ish/Alpine)
  if ! command -v getent &>/dev/null && grep -q "^${CLIENT_GROUP}:" /etc/group 2>/dev/null; then
    return 0
  fi
  
  # Prefer groupadd (standard Linux), fall back to addgroup (Alpine/BusyBox)
  if command -v groupadd &>/dev/null; then
    groupadd -r "$CLIENT_GROUP"
    echo "info: created system group $CLIENT_GROUP (chat SSH users join via admin-add-user.sh)"
  elif command -v addgroup &>/dev/null; then
    addgroup -S "$CLIENT_GROUP" 2>/dev/null || addgroup "$CLIENT_GROUP"
    echo "info: created system group $CLIENT_GROUP (chat SSH users join via admin-add-user.sh)"
  else
    echo "error: neither groupadd nor addgroup found (cannot create $CLIENT_GROUP)" >&2
    exit 1
  fi
}

apply_data_plane_permissions() {
  # Chat login users only need chat.sh, client.py, sshchat.env, venv/. Admins keep
  # server.* and admin-add-user.sh private to root / service user.
  local u="$RUN_USER"
  local g
  g=$(primary_group_of "$u")
  chown "$u:$CLIENT_GROUP" "$PREFIX"
  chmod 750 "$PREFIX"

  chown "$u:$CLIENT_GROUP" "$PREFIX/chat.sh" "$PREFIX/client.py"
  chmod 750 "$PREFIX/chat.sh"
  chmod 640 "$PREFIX/client.py"
  if [[ -f "$PREFIX/sshchat.env" ]]; then
    chown "$u:$CLIENT_GROUP" "$PREFIX/sshchat.env"
    chmod 640 "$PREFIX/sshchat.env"
  fi

  chown "$u:$g" "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py" "$PREFIX/server.sh"
  chmod 600 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py"
  chmod 700 "$PREFIX/server.sh"
  if [[ -f "$PREFIX/game_ratings.json" ]]; then
    chown "$u:$g" "$PREFIX/game_ratings.json"
    chmod 660 "$PREFIX/game_ratings.json"
  fi
  if [[ -f "$PREFIX/offline_messages.json" ]]; then
    chown "$u:$g" "$PREFIX/offline_messages.json"
    chmod 660 "$PREFIX/offline_messages.json"
  fi

  chown "$ROOT_OWN" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh"
  chmod 700 "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh"
  # Bridge runs as sshchat-federation via forced-command; must be executable by that user.
  chown "$ROOT_OWN" "$PREFIX/federation-bridge.sh"
  chmod 755 "$PREFIX/federation-bridge.sh"

  chown -R "$u:$CLIENT_GROUP" "$PREFIX/venv"
  chmod -R 'u=rwX,g=rX,o=-' "$PREFIX/venv"

  # Library directory: readable by client group so users can browse books
  if [[ -d "$PREFIX/library" ]]; then
    chown "$u:$CLIENT_GROUP" "$PREFIX/library"
    chmod 750 "$PREFIX/library"
  fi

  if [[ -d /var/lib/sshchat/files ]]; then
    chown -R "$u:$g" /var/lib/sshchat/files
    chmod 750 /var/lib/sshchat/files
  fi

  apply_federation_permissions
}

# Federation needs two identities:
# - RUN_USER (sshchat): owns PREFIX/federation (peers.json + private key)
# - sshchat-federation: separate home for inbound SSH (sshd requires home owned
#   by that user — cannot share PREFIX/federation with the service user)
apply_federation_permissions() {
  local fed_dir="$PREFIX/federation"
  local fed_user=${SSHCHAT_FEDERATION_USER:-sshchat-federation}
  local fed_home=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}
  local svc_user="$RUN_USER"
  local svc_group fed_group
  svc_group=$(primary_group_of "$svc_user")
  [[ -d "$fed_dir" ]] || mkdir -p "$fed_dir"

  # Service data dir: only the chat service needs it (not sshd home).
  if [[ "$svc_user" != "root" ]] && id "$svc_user" &>/dev/null; then
    chown "$svc_user:$svc_group" "$fed_dir"
  fi
  chmod 750 "$fed_dir"

  if [[ -f "$fed_dir/id_ed25519" ]]; then
    if [[ "$svc_user" != "root" ]] && id "$svc_user" &>/dev/null; then
      chown "$svc_user:$svc_group" "$fed_dir/id_ed25519"
    fi
    chmod 600 "$fed_dir/id_ed25519"
  fi
  if [[ -f "$fed_dir/id_ed25519.pub" ]]; then
    if [[ "$svc_user" != "root" ]] && id "$svc_user" &>/dev/null; then
      chown "$svc_user:$svc_group" "$fed_dir/id_ed25519.pub"
    fi
    chmod 644 "$fed_dir/id_ed25519.pub"
  fi
  if [[ -f "$fed_dir/peers.json" ]]; then
    if [[ "$svc_user" != "root" ]] && id "$svc_user" &>/dev/null; then
      chown "$svc_user:$svc_group" "$fed_dir/peers.json"
    fi
    chmod 640 "$fed_dir/peers.json"
  fi

  if ! is_darwin && id "$fed_user" &>/dev/null; then
    fed_group=$(primary_group_of "$fed_user")
    add_user_to_client_group "$fed_user" || true
    local cur_home
    cur_home=$(getent passwd "$fed_user" | cut -d: -f6)
    mkdir -p "$fed_home"
    # sshd: home must be owned by the user (or root) and not group/other-writable.
    chown "$fed_user:$fed_group" "$fed_home"
    chmod 755 "$fed_home"
    if [[ -n "$cur_home" && "$cur_home" != "$fed_home" ]]; then
      # Migrate inbound keys from the old home (often PREFIX/federation).
      if [[ -f "$cur_home/.ssh/authorized_keys" && ! -f "$fed_home/.ssh/authorized_keys" ]]; then
        mkdir -p "$fed_home/.ssh"
        cp -a "$cur_home/.ssh/authorized_keys" "$fed_home/.ssh/authorized_keys"
      fi
      if command -v usermod >/dev/null 2>&1; then
        usermod -d "$fed_home" "$fed_user" 2>/dev/null || true
        echo "info: federation SSH home -> $fed_home (was $cur_home)"
      fi
    fi
    mkdir -p "$fed_home/.ssh"
    chown "$fed_user:$fed_group" "$fed_home/.ssh"
    chmod 700 "$fed_home/.ssh"
    if [[ -f "$fed_home/.ssh/authorized_keys" ]]; then
      chown "$fed_user:$fed_group" "$fed_home/.ssh/authorized_keys"
      chmod 600 "$fed_home/.ssh/authorized_keys"
    fi
  fi
}

apply_root_group_permissions() {
  chown "root:$CLIENT_GROUP" "$PREFIX"
  chmod 750 "$PREFIX"

  chown "root:$CLIENT_GROUP" "$PREFIX/chat.sh" "$PREFIX/client.py"
  chmod 750 "$PREFIX/chat.sh"
  chmod 640 "$PREFIX/client.py"
  if [[ -f "$PREFIX/sshchat.env" ]]; then
    chown "root:$CLIENT_GROUP" "$PREFIX/sshchat.env"
    chmod 640 "$PREFIX/sshchat.env"
  fi

  chown "$ROOT_OWN" "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh"
  chmod 600 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py"
  chmod 700 "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh"
  chown "$ROOT_OWN" "$PREFIX/federation-bridge.sh"
  chmod 755 "$PREFIX/federation-bridge.sh"
  if [[ -f "$PREFIX/game_ratings.json" ]]; then
    chown "$ROOT_OWN" "$PREFIX/game_ratings.json"
    chmod 660 "$PREFIX/game_ratings.json"
  fi
  if [[ -f "$PREFIX/offline_messages.json" ]]; then
    chown "$ROOT_OWN" "$PREFIX/offline_messages.json"
    chmod 660 "$PREFIX/offline_messages.json"
  fi

  chown -R "root:$CLIENT_GROUP" "$PREFIX/venv"
  chmod -R 'u=rwX,g=rX,o=-' "$PREFIX/venv"
  
  # Library directory: readable by client group so users can browse books
  if [[ -d "$PREFIX/library" ]]; then
    chown "root:$CLIENT_GROUP" "$PREFIX/library"
    chmod 750 "$PREFIX/library"
  fi
  
  apply_federation_permissions
}

user_in_group() {
  local user_name=$1
  if is_darwin; then
    id -Gn "$user_name" 2>/dev/null | tr ' ' '\n' | grep -qx "$CLIENT_GROUP"
  else
    id -nG "$user_name" 2>/dev/null | tr ' ' '\n' | grep -qx "$CLIENT_GROUP"
  fi
}

add_user_to_client_group() {
  local user_name=$1
  [[ -n "$user_name" ]] || return 0
  id "$user_name" &>/dev/null || return 0
  if user_in_group "$user_name"; then
    return 0
  fi
  if is_darwin; then
    dseditgroup -o edit -a "$user_name" -t user "$CLIENT_GROUP"
  elif command -v usermod &>/dev/null; then
    usermod -aG "$CLIENT_GROUP" "$user_name"
  elif command -v addgroup &>/dev/null; then
    # Alpine/BusyBox: addgroup USER GROUP (adds user to existing group)
    addgroup "$user_name" "$CLIENT_GROUP" 2>/dev/null || true
  else
    echo "warning: cannot add $user_name to $CLIENT_GROUP (no usermod or addgroup)" >&2
    return 1
  fi
  echo "info: added existing user $user_name to group $CLIENT_GROUP"
}

sync_existing_chat_users_to_group() {
  local chat_abs=$1
  local bn=${SSHCHAT_COMMAND_BASENAME:-$(basename "$chat_abs")}
  local f user_name
  shopt -s nullglob
  if is_darwin; then
    for f in /Users/*/.ssh/authorized_keys; do
      grep -qF "$chat_abs" "$f" 2>/dev/null || grep -qE "command=[\"'](/[^\"']*/)?$bn[\"']" "$f" 2>/dev/null || continue
      user_name=${f#/Users/}
      user_name=${user_name%%/*}
      add_user_to_client_group "$user_name"
    done
  else
    for f in /home/*/.ssh/authorized_keys; do
      grep -qF "$chat_abs" "$f" 2>/dev/null || grep -qE "command=[\"'](/[^\"']*/)?$bn[\"']" "$f" 2>/dev/null || continue
      user_name=${f#/home/}
      user_name=${user_name%%/*}
      add_user_to_client_group "$user_name"
    done
  fi
  shopt -u nullglob
}

migrate_authorized_keys_chat_command() {
  local chat_abs=$1
  local bn=${SSHCHAT_COMMAND_BASENAME:-$(basename "$chat_abs")}

  if ! command -v perl &>/dev/null; then
    echo "warning: perl not found; skipping authorized_keys migration (install perl or use --no-migrate-keys)" >&2
    return 0
  fi

  do_one_authkeys() {
    local path=$1
    local own
    [[ -f "$path" ]] || return 0
    grep -q 'command=' "$path" || return 0
    grep -qF "$bn" "$path" || return 0
    if [[ "$(uname -s)" == "Darwin" ]]; then
      own=$(stat -f '%u:%g' "$path")
    else
      own=$(stat -c '%u:%g' "$path")
    fi
    SSHCHAT_MIGRATE_NEW="$chat_abs" SSHCHAT_CMD_BN="$bn" perl -i.bak -pe "$(cat <<'ENDPERL'
BEGIN {
  $new = $ENV{SSHCHAT_MIGRATE_NEW};
  die "internal" unless defined $new && length $new;
  $b = $ENV{SSHCHAT_CMD_BN} || "chat.sh";
  $b =~ s/\./\\./g;
}
s/(command=")(\/[^"]*$b)(")/$1$new$3/g;
s/(command=\x27)(\/[^\x27]*$b)(\x27)/$1$new$3/g;
ENDPERL
)" "$path"
    chown "$own" "$path"
    [[ -e "${path}.bak" ]] && chown "$own" "${path}.bak"
    echo "info: migrated command= paths in $path -> $chat_abs"
  }

  local f
  shopt -s nullglob
  if [[ "$(uname -s)" == "Darwin" ]]; then
    for f in /Users/*/.ssh/authorized_keys; do
      do_one_authkeys "$f"
    done
    do_one_authkeys /var/root/.ssh/authorized_keys
  else
    for f in /home/*/.ssh/authorized_keys; do
      do_one_authkeys "$f"
    done
    do_one_authkeys /root/.ssh/authorized_keys
  fi
  shopt -u nullglob
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX=${2:?}
      shift 2
      ;;
    --server-ip)
      SERVER_IP=${2:?}
      shift 2
      ;;
    --port)
      PORT=${2:?}
      shift 2
      ;;
    --keep-env)
      KEEP_ENV=1
      shift
      ;;
    --no-migrate-keys)
      MIGRATE_KEYS=0
      shift
      ;;
    --no-systemd)
      INSTALL_SYSTEMD=0
      shift
      ;;
    --no-openrc)
      INSTALL_OPENRC=0
      shift
      ;;
    --run-user)
      RUN_USER=${2:?}
      shift 2
      ;;
    --no-run-user)
      CREATE_RUN_USER=0
      shift
      ;;
    --client-ssh-host)
      CLIENT_SSH_HOST=${2:?}
      shift 2
      ;;
    --client-ssh-port)
      CLIENT_SSH_PORT=${2:?}
      shift 2
      ;;
    --build-gui-packages)
      BUILD_GUI_PACKAGES=1
      shift
      ;;
    --reset-all-ratings)
      RESET_ALL_RATINGS=1
      shift
      ;;
    --reset-game-ratings)
      RESET_GAME_RATINGS=${2:?}
      shift 2
      ;;
    --reset-user-game-rating)
      RESET_USER_RATING_USER=${2:?}
      RESET_USER_RATING_GAME=${3:?}
      shift 3
      ;;
    --pip-index-url)
      PIP_INDEX_URL_ARG="$2"
      shift 2
      ;;
    --file-domain)
      FILE_DOMAIN=${2:?}
      shift 2
      ;;
    --file-port)
      FILE_HTTP_PORT=${2:?}
      shift 2
      ;;
    --no-file-https)
      FILE_USE_HTTPS=0
      shift
      ;;
    --no-file-transfer)
      FILE_TRANSFER_ENABLED=0
      shift
      ;;
    --cloudflare)
      USE_CLOUDFLARE=1
      CLOUDFLARE_EXPLICIT=1
      shift
      ;;
    --no-cloudflare)
      USE_CLOUDFLARE=0
      CLOUDFLARE_EXPLICIT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

reset_count=0
[[ "$RESET_ALL_RATINGS" -eq 1 ]] && reset_count=$((reset_count + 1))
[[ -n "$RESET_GAME_RATINGS" ]] && reset_count=$((reset_count + 1))
[[ -n "$RESET_USER_RATING_USER" ]] && reset_count=$((reset_count + 1))
if [[ "$reset_count" -gt 1 ]]; then
  echo "error: choose only one rating reset option per deploy run" >&2
  exit 1
fi

if is_darwin && [[ "${SSHCHAT_NO_MAC_ADAPT:-}" != "1" ]]; then
  if [[ "$CREATE_RUN_USER" -eq 1 ]]; then
    echo "info: macOS: local-dev install (no Linux service user/systemd; uses group $CLIENT_GROUP for chat access)" >&2
    echo "info: macOS: use $PREFIX/server.sh to run the server; set SSHCHAT_SERVER in $PREFIX/sshchat.env for LAN clients" >&2
    CREATE_RUN_USER=0
    INSTALL_SYSTEMD=0
  fi
fi

if is_ish && [[ "${SSHCHAT_NO_ISH_ADAPT:-}" != "1" ]]; then
  echo "info: iSH detected (Alpine under iOS emulator)" >&2
  INSTALL_SYSTEMD=0
  # cloudflared has no i686 build; LAN / --file-domain only unless forced.
  if [[ "$CLOUDFLARE_EXPLICIT" -eq 0 && "$USE_CLOUDFLARE" -eq 1 ]]; then
    USE_CLOUDFLARE=0
    FILE_USE_HTTPS=0
    echo "info: iSH: disabling Cloudflare Quick Tunnel (no i686 cloudflared); use LAN --client-ssh-host or --file-domain" >&2
  fi
  # Prefer a China-friendly mirror when none set (iSH networking is slow/fragile).
  if [[ -z "$PIP_INDEX_URL_ARG" ]]; then
    PIP_INDEX_URL_ARG="https://pypi.tuna.tsinghua.edu.cn/simple"
    echo "info: iSH: defaulting pip index to $PIP_INDEX_URL_ARG" >&2
  fi
  # Fresh pip upgrade often hangs for many minutes on iSH; skip unless asked.
  if [[ -z "${SSHCHAT_SKIP_PIP_UPGRADE:-}" ]]; then
    export SSHCHAT_SKIP_PIP_UPGRADE=1
    echo "info: iSH: skipping pip self-upgrade (set SSHCHAT_SKIP_PIP_UPGRADE=0 to force)" >&2
  fi
  # Give pip more room; large downloads + ensurepip are slow under the emulator.
  if [[ -z "${SSHCHAT_PIP_TIMEOUT:-}" ]]; then
    PIP_TIMEOUT=300
  fi
  if [[ -z "${SSHCHAT_PIP_CMD_RETRIES:-}" ]]; then
    PIP_CMD_RETRIES=5
  fi
  # Soft deps from Alpine apk so venv can use --system-site-packages (avoids compiling lxml).
  if command -v apk &>/dev/null; then
    echo "info: iSH: ensuring apk packages py3-lxml py3-pip" >&2
    apk add --no-cache py3-lxml py3-pip 2>/dev/null || apk add py3-lxml py3-pip || true
  fi
fi

if [[ "$FILE_TRANSFER_ENABLED" -eq 0 ]]; then
  USE_CLOUDFLARE=0
fi
if [[ -n "$FILE_DOMAIN" && "$USE_CLOUDFLARE" -eq 1 ]]; then
  echo "info: --file-domain set; skipping Cloudflare tunnel (Let's Encrypt mode)" >&2
  USE_CLOUDFLARE=0
fi
if [[ "$USE_CLOUDFLARE" -eq 1 ]]; then
  FILE_USE_HTTPS=0
  echo "info: Cloudflare Quick Tunnel enabled for /sendfile (disable with --no-cloudflare)" >&2
fi

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

for f in server.py client.py games.py ratings.py sgs_data.py library.py dict_lookup.py session_store.py federation.py offline_messages.py chat.sh server.sh admin-add-user.sh admin-add-peer.sh admin-remove-peer.sh federation-bridge.sh; do
  [[ -f "$SCRIPT_DIR/$f" ]] || { echo "error: missing $SCRIPT_DIR/$f" >&2; exit 1; }
done

chmod +x \
  "$SCRIPT_DIR/chat.sh" \
  "$SCRIPT_DIR/server.sh" \
  "$SCRIPT_DIR/admin-add-user.sh" \
  "$SCRIPT_DIR/admin-add-peer.sh" \
  "$SCRIPT_DIR/admin-remove-peer.sh" \
  "$SCRIPT_DIR/federation-bridge.sh"

if ! command -v python3 &>/dev/null; then
  echo "error: python3 not found" >&2
  exit 1
fi
if ! python3 -c "import venv" 2>/dev/null; then
  echo "error: python3 venv module missing (e.g. apt install python3-venv)" >&2
  exit 1
fi

if [[ -z "$SERVER_IP" ]]; then
  # In forced-command SSH mode, client.py runs on this host after login, so loopback
  # is the safest default regardless of where users SSH from.
  SERVER_IP="127.0.0.1"
  echo "info: defaulting SSHCHAT_SERVER to 127.0.0.1 (forced-command local client mode)" >&2
fi

if ! [[ "$CLIENT_SSH_PORT" =~ ^[0-9]+$ ]]; then
  echo "error: client SSH port must be numeric (got: $CLIENT_SSH_PORT)" >&2
  exit 1
fi

if [[ -z "$CLIENT_SSH_HOST" ]]; then
  if [[ -n "$SERVER_IP" && "$SERVER_IP" != "127.0.0.1" ]]; then
    CLIENT_SSH_HOST="$SERVER_IP"
  else
    CLIENT_SSH_HOST=$(detect_ip)
  fi
fi
if [[ "$CLIENT_SSH_HOST" == "127.0.0.1" ]]; then
  echo "warning: client-bundle.json SSH host is 127.0.0.1; remote users cannot reach it — set --client-ssh-host to your public DNS/IP" >&2
fi

mkdir -p "$PREFIX"
if [[ "$CREATE_RUN_USER" -eq 1 ]] || is_darwin; then
  ensure_client_group
fi

if [[ "$CREATE_RUN_USER" -eq 1 ]]; then
  if ! id "$RUN_USER" &>/dev/null; then
    # Prefer useradd (standard Linux), fall back to adduser (Alpine/BusyBox)
    if command -v useradd &>/dev/null; then
      useradd -r -s /usr/sbin/nologin "$RUN_USER"
      echo "info: created system user $RUN_USER"
    elif command -v adduser &>/dev/null; then
      # Alpine: create a same-named group first so later chown user:user works.
      if command -v addgroup &>/dev/null && ! getent group "$RUN_USER" &>/dev/null 2>&1; then
        addgroup -S "$RUN_USER" 2>/dev/null || addgroup "$RUN_USER" || true
      fi
      if getent group "$RUN_USER" &>/dev/null 2>&1; then
        adduser -S -D -G "$RUN_USER" -s /sbin/nologin "$RUN_USER" 2>/dev/null || \
          adduser -S -D -G "$RUN_USER" -s /bin/false "$RUN_USER"
      else
        adduser -S -D -s /sbin/nologin "$RUN_USER" 2>/dev/null || adduser -S -D -s /bin/false "$RUN_USER"
      fi
      echo "info: created system user $RUN_USER (group $(primary_group_of "$RUN_USER"))"
    else
      echo "error: neither useradd nor adduser found; install shadow-utils or use --no-run-user" >&2
      exit 1
    fi
  fi
else
  RUN_USER=root
  if [[ "$INSTALL_SYSTEMD" -eq 1 ]]; then
    echo "error: systemd install needs a non-root service user; omit --no-run-user or use --no-systemd" >&2
    exit 1
  fi
fi

if [[ "$INSTALL_SYSTEMD" -eq 1 && "$RUN_USER" == "root" ]]; then
  echo "error: refuse root service user with systemd; use default --run-user sshchat" >&2
  exit 1
fi

install -m 0755 -d "$PREFIX"
install -m 0755 -d "$PREFIX/library"
# Ensure no stale interpreter is still importing the old server.py/games.py.
stop_existing_server "$PREFIX"
cp -f "$SCRIPT_DIR/server.py" "$SCRIPT_DIR/client.py" "$SCRIPT_DIR/sshchat_client_util.py" "$SCRIPT_DIR/games.py" "$SCRIPT_DIR/ratings.py" "$SCRIPT_DIR/sgs_data.py" "$SCRIPT_DIR/library.py" "$SCRIPT_DIR/dict_lookup.py" "$SCRIPT_DIR/session_store.py" "$SCRIPT_DIR/federation.py" "$SCRIPT_DIR/offline_messages.py" "$SCRIPT_DIR/file_sharing.py" "$SCRIPT_DIR/file_http_server.py" "$PREFIX/"
cp -f "$SCRIPT_DIR/chat.sh" "$SCRIPT_DIR/server.sh" "$SCRIPT_DIR/admin-add-user.sh" "$SCRIPT_DIR/admin-add-peer.sh" "$SCRIPT_DIR/admin-remove-peer.sh" "$SCRIPT_DIR/federation-bridge.sh" "$PREFIX/"
chmod +x "$PREFIX/chat.sh" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"
# Drop any stale .pyc / __pycache__ so the next import never resurrects an
# older games.py / server.py from cache.
find "$PREFIX" -maxdepth 2 -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
find "$PREFIX" -maxdepth 2 -name '*.pyc' -delete 2>/dev/null || true

REUSE_VENV=0
if is_ish && [[ -x "$PREFIX/venv/bin/python" ]]; then
  if "$PREFIX/venv/bin/python" -c "import ebooklib, lxml, prompt_toolkit, chess, pypdf" 2>/dev/null; then
    REUSE_VENV=1
    echo "info: iSH: reusing existing venv (deps already importable)" >&2
  fi
fi

if [[ "$REUSE_VENV" -eq 0 ]]; then
  rm -rf "$PREFIX/venv"
  # Use temp space under PREFIX: macOS /private/tmp can be tight; pip unpacks wheels there by default.
  DEPLOY_TMP="$PREFIX/.deploy-tmp"
  rm -rf "$DEPLOY_TMP"
  mkdir -p "$DEPLOY_TMP"
  export TMPDIR="$DEPLOY_TMP"
  export PIP_NO_CACHE_DIR=1

  VENV_ARGS=()
  REQ_FILE="$SCRIPT_DIR/requirements-server.txt"
  if is_ish; then
    # Reuse Alpine py3-lxml (and any other system site packages) so ebooklib
    # does not compile lxml from source under the i686 emulator.
    VENV_ARGS+=(--system-site-packages)
    if [[ -f "$SCRIPT_DIR/requirements-server-ish.txt" ]]; then
      REQ_FILE="$SCRIPT_DIR/requirements-server-ish.txt"
      echo "info: iSH: using $REQ_FILE (pymupdf skipped; PDF via pypdf)" >&2
    fi
  fi

  echo "info: creating venv at $PREFIX/venv (on iSH this can take several minutes)..."
  python3 -m venv "${VENV_ARGS[@]}" "$PREFIX/venv"

  PIP_COMMON_ARGS=(--timeout "$PIP_TIMEOUT" --retries "$PIP_RETRIES")
  if [[ -n "$PIP_INDEX_URL_ARG" ]]; then
    PIP_COMMON_ARGS+=(--index-url "$PIP_INDEX_URL_ARG")
    echo "info: using pip index $PIP_INDEX_URL_ARG (timeout=${PIP_TIMEOUT}s, retries=${PIP_RETRIES})"
  else
    echo "info: using default pip index (timeout=${PIP_TIMEOUT}s, retries=${PIP_RETRIES}); on slow links use --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple"
  fi

  # Fresh venv ships with old pip (e.g. 21.x) that can fail hash checks when PyPI
  # republishes wheels (pymupdf 1.28.0). Upgrade before requirements unless skipped.
  if [[ "${SSHCHAT_SKIP_PIP_UPGRADE:-0}" != "1" ]]; then
    echo "info: upgrading pip in $PREFIX/venv"
    pip_run_with_retry "$PREFIX/venv/bin/python" -m pip install -q "${PIP_COMMON_ARGS[@]}" --upgrade pip
  fi
  # Prefer binary wheels; never try to build pymupdf/lxml from source on constrained hosts.
  echo "info: installing Python deps from $REQ_FILE"
  if is_ish; then
    # Install pure-python / wheel deps first; ebooklib needs lxml which comes from apk
    # via --system-site-packages — avoid pip compiling lxml for i686.
    pip_run_with_retry "$PREFIX/venv/bin/pip" install -q "${PIP_COMMON_ARGS[@]}" --prefer-binary \
      prompt_toolkit 'chess>=1.10' 'pypdf>=4.0'
    pip_run_with_retry "$PREFIX/venv/bin/pip" install -q "${PIP_COMMON_ARGS[@]}" --prefer-binary --no-deps \
      'ebooklib>=0.18'
    if ! "$PREFIX/venv/bin/python" -c "import ebooklib, lxml, prompt_toolkit, chess, pypdf" 2>/dev/null; then
      echo "error: iSH venv missing required modules after install" >&2
      "$PREFIX/venv/bin/python" -c "import ebooklib, lxml, prompt_toolkit, chess, pypdf"
      exit 1
    fi
    echo "info: iSH Python deps OK (ebooklib uses system py3-lxml)"
  else
    pip_run_with_retry "$PREFIX/venv/bin/pip" install -q "${PIP_COMMON_ARGS[@]}" --prefer-binary -r "$REQ_FILE"
  fi
  rm -rf "$DEPLOY_TMP"
else
  echo "info: skipped venv recreate / pip install"
fi

umask 022
if [[ "$KEEP_ENV" -eq 1 && -f "$PREFIX/sshchat.env" ]]; then
  echo "info: keeping existing $PREFIX/sshchat.env (--keep-env)"
else
  if [[ "$USE_CLOUDFLARE" -eq 1 ]]; then
    cat >"$PREFIX/sshchat.env" <<EOF
SSHCHAT_SERVER=$SERVER_IP
SSHCHAT_PORT=$PORT
SSHCHAT_FEDERATION_PORT=$((PORT + 1))
SSHCHAT_NODE_ID=$(hostname -f 2>/dev/null || hostname)
SSHCHAT_ALERT_SOUND=auto
# 联邦互联：互信节点用 admin-add-peer.sh / admin-remove-peer.sh 登记或拆除；同名用户/房间跨服合并。
# 禁用联邦：SSHCHAT_FEDERATION_DISABLE=1
# /news RSS：默认经本机 HTTP 代理 127.0.0.1:7897（见 server.py NEWS_PROXY_LOCAL_DEFAULT）。
# 若聊天服务跑在远端且无本地代理，请设 SSHCHAT_NEWS_NO_PROXY=1，或设 SSHCHAT_NEWS_PROXY=你的代理地址。
# 图书馆目录（epub / txt / pdf）：默认 $PREFIX/library

# 文件传输：本机 HTTP + Cloudflare Quick Tunnel（公网 https://*.trycloudflare.com）
SSHCHAT_FILE_TRANSFER_ENABLED=$FILE_TRANSFER_ENABLED
SSHCHAT_FILE_HTTP_HOST=127.0.0.1
SSHCHAT_FILE_HTTP_PORT=$FILE_HTTP_PORT
SSHCHAT_FILE_USE_HTTPS=0
SSHCHAT_FILE_DOMAIN=
# 隧道起来后由 sshchat-cloudflared 自动改写为 *.trycloudflare.com
SSHCHAT_FILE_PUBLIC_HOST=$CLIENT_SSH_HOST
SSHCHAT_FILE_PUBLIC_PORT=443
SSHCHAT_FILE_STORAGE_DIR=$FILE_STORAGE_DIR
# 最大文件大小（字节）：默认 100MB
# SSHCHAT_MAX_FILE_SIZE=104857600
# 下载链接是否只能用一次：默认 1（下载完成即作废）。设 0 会削弱安全性，不推荐
# SSHCHAT_ONE_TIME_DOWNLOAD=0
# 预览/下载一次性链接的有效期（秒）：默认 600
# SSHCHAT_TICKET_TTL_SECONDS=600
# 超过此大小不做在线预览，直接走下载：默认 25MB
# SSHCHAT_MAX_PREVIEW_SIZE=26214400
EOF
  else
    cat >"$PREFIX/sshchat.env" <<EOF
SSHCHAT_SERVER=$SERVER_IP
SSHCHAT_PORT=$PORT
SSHCHAT_FEDERATION_PORT=$((PORT + 1))
SSHCHAT_NODE_ID=$(hostname -f 2>/dev/null || hostname)
SSHCHAT_ALERT_SOUND=auto
# 联邦互联：互信节点用 admin-add-peer.sh / admin-remove-peer.sh 登记或拆除；同名用户/房间跨服合并。
# 禁用联邦：SSHCHAT_FEDERATION_DISABLE=1
# /news RSS：默认经本机 HTTP 代理 127.0.0.1:7897（见 server.py NEWS_PROXY_LOCAL_DEFAULT）。
# 若聊天服务跑在远端且无本地代理，请设 SSHCHAT_NEWS_NO_PROXY=1，或设 SSHCHAT_NEWS_PROXY=你的代理地址。
# 图书馆目录（epub / txt / pdf）：默认 $PREFIX/library

# 文件传输配置
SSHCHAT_FILE_TRANSFER_ENABLED=$FILE_TRANSFER_ENABLED
SSHCHAT_FILE_HTTP_PORT=$FILE_HTTP_PORT
SSHCHAT_FILE_USE_HTTPS=$FILE_USE_HTTPS
SSHCHAT_FILE_DOMAIN=$FILE_DOMAIN
# 发给用户的文件网址用哪个主机名（监听地址是 0.0.0.0，不能直接给用户）
SSHCHAT_FILE_PUBLIC_HOST=$CLIENT_SSH_HOST
# 文件存储目录：默认 /tmp/sshchat_files
# SSHCHAT_FILE_STORAGE_DIR=/var/lib/sshchat/files
# 最大文件大小（字节）：默认 100MB
# SSHCHAT_MAX_FILE_SIZE=104857600
# 下载链接是否只能用一次：默认 1（下载完成即作废）。设 0 会削弱安全性，不推荐
# SSHCHAT_ONE_TIME_DOWNLOAD=0
# 预览/下载一次性链接的有效期（秒）：默认 600
# SSHCHAT_TICKET_TTL_SECONDS=600
# 超过此大小不做在线预览，直接走下载：默认 25MB
# SSHCHAT_MAX_PREVIEW_SIZE=26214400
EOF
  fi
fi

if [[ "$USE_CLOUDFLARE" -eq 1 ]]; then
  mkdir -p "$FILE_STORAGE_DIR" /var/lib/sshchat/cloudflared
  # Even with --keep-env, force tunnel-compatible local listener settings.
  if [[ -f "$PREFIX/sshchat.env" ]]; then
    python3 - <<PY
from pathlib import Path
p = Path("$PREFIX/sshchat.env")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
keys = {
    "SSHCHAT_FILE_TRANSFER_ENABLED": "1",
    "SSHCHAT_FILE_HTTP_HOST": "127.0.0.1",
    "SSHCHAT_FILE_HTTP_PORT": "$FILE_HTTP_PORT",
    "SSHCHAT_FILE_USE_HTTPS": "0",
    "SSHCHAT_FILE_PUBLIC_PORT": "443",
    "SSHCHAT_FILE_STORAGE_DIR": "$FILE_STORAGE_DIR",
}
seen = set()
out = []
for line in lines:
    if line.startswith("# File transfer via Cloudflare Tunnel"):
        continue
    if "=" in line and not line.lstrip().startswith("#"):
        k = line.split("=", 1)[0]
        if k in keys:
            out.append(f"{k}={keys[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in keys.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\\n".join(out) + "\\n", encoding="utf-8")
PY
  fi
fi

FED_DIR="$PREFIX/federation"
FED_USER=sshchat-federation
FED_HOME=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}
mkdir -p "$FED_DIR"
chmod 750 "$FED_DIR"
if [[ ! -f "$FED_DIR/id_ed25519" ]]; then
  ssh-keygen -t ed25519 -f "$FED_DIR/id_ed25519" -N "" -C "sshchat-federation@$(hostname -f 2>/dev/null || hostname)" >/dev/null
  echo "info: generated federation key $FED_DIR/id_ed25519.pub"
fi
if [[ "$CREATE_RUN_USER" -eq 1 ]] && ! is_darwin; then
  if ! id "$FED_USER" &>/dev/null; then
    mkdir -p "$FED_HOME"
    # Prefer useradd (standard Linux), fall back to adduser (Alpine/BusyBox)
    if command -v useradd &>/dev/null; then
      useradd -r -s /usr/sbin/nologin -d "$FED_HOME" -c "SSHChat federation" "$FED_USER" 2>/dev/null || true
    elif command -v adduser &>/dev/null; then
      adduser -S -D -h "$FED_HOME" -s /sbin/nologin -g "SSHChat federation" "$FED_USER" 2>/dev/null || \
        adduser -S -D -h "$FED_HOME" -s /bin/false "$FED_USER" 2>/dev/null || true
    fi
    if id "$FED_USER" &>/dev/null; then
      echo "info: created federation user $FED_USER (home $FED_HOME)"
    fi
  fi
  if id "$FED_USER" &>/dev/null; then
    add_user_to_client_group "$FED_USER" || true
  fi
fi
apply_federation_permissions

if [[ "$RESET_ALL_RATINGS" -eq 1 ]]; then
  echo "info: resetting all persisted board-game ratings"
  "$PREFIX/server.sh" --reset-ratings-all
elif [[ -n "$RESET_GAME_RATINGS" ]]; then
  echo "info: resetting persisted ratings for game $RESET_GAME_RATINGS"
  "$PREFIX/server.sh" --reset-ratings-game "$RESET_GAME_RATINGS"
elif [[ -n "$RESET_USER_RATING_USER" ]]; then
  echo "info: resetting persisted rating for user $RESET_USER_RATING_USER game $RESET_USER_RATING_GAME"
  "$PREFIX/server.sh" --reset-ratings-user-game "$RESET_USER_RATING_USER" "$RESET_USER_RATING_GAME"
fi

CLIENT_BUNDLE_JSON="$PREFIX/client-bundle.json"
SSHCHAT__H="$CLIENT_SSH_HOST" SSHCHAT__P="$CLIENT_SSH_PORT" SSHCHAT__OUT="$CLIENT_BUNDLE_JSON" python3 - <<'PY'
import json, os, pathlib
out = os.environ["SSHCHAT__OUT"]
obj = {
    "host": os.environ["SSHCHAT__H"],
    "ssh_port": int(os.environ["SSHCHAT__P"]),
    "bundle_mode": True,
}
pathlib.Path(out).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
chmod 644 "$CLIENT_BUNDLE_JSON"
chown "$ROOT_OWN" "$CLIENT_BUNDLE_JSON" 2>/dev/null || true

if [[ -d "$SCRIPT_DIR" ]]; then
  if mkdir -p "$SCRIPT_DIR/dist" 2>/dev/null && cp -f "$CLIENT_BUNDLE_JSON" "$SCRIPT_DIR/dist/client-bundle.json" 2>/dev/null; then
    chmod 644 "$SCRIPT_DIR/dist/client-bundle.json" 2>/dev/null || true
    echo "info: copied client-bundle.json -> $SCRIPT_DIR/dist/ (for maintainer GUI builds)"
  else
    echo "info: could not write $SCRIPT_DIR/dist/client-bundle.json (copy from $CLIENT_BUNDLE_JSON manually)" >&2
  fi
fi

if [[ "$CREATE_RUN_USER" -eq 1 ]]; then
  apply_data_plane_permissions
elif is_darwin; then
  apply_root_group_permissions
else
  chown -R "$ROOT_OWN" "$PREFIX"
  chmod 755 "$PREFIX"
  chmod 755 "$PREFIX/chat.sh" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh"
  chmod 755 "$PREFIX/federation-bridge.sh"
  chmod 644 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/client.py"
  [[ -f "$PREFIX/sshchat.env" ]] && chmod 644 "$PREFIX/sshchat.env"
  # Library directory should be accessible by client group
  if [[ -d "$PREFIX/library" ]]; then
    chown "root:$CLIENT_GROUP" "$PREFIX/library"
    chmod 750 "$PREFIX/library"
  fi
  apply_federation_permissions
fi

CHAT_ABS=$(cd "$(dirname "$PREFIX/chat.sh")" && pwd)/$(basename "$PREFIX/chat.sh")
if [[ "$MIGRATE_KEYS" -eq 1 ]]; then
  echo "info: rewriting authorized_keys command= (basename: ${SSHCHAT_COMMAND_BASENAME:-$(basename "$CHAT_ABS")}) -> $CHAT_ABS"
  migrate_authorized_keys_chat_command "$CHAT_ABS"
fi
echo "info: ensuring existing chat users have group $CLIENT_GROUP"
sync_existing_chat_users_to_group "$CHAT_ABS"

UNIT=/etc/systemd/system/sshchat.service
if [[ "$INSTALL_SYSTEMD" -eq 1 ]] && command -v systemctl &>/dev/null && [[ -d /run/systemd/system || -d /lib/systemd/system ]]; then
  SVC_USER=""
  if [[ "$RUN_USER" != "root" ]]; then
    SVC_USER="User=$RUN_USER
Group=$RUN_USER
"
  fi
  cat >"$UNIT" <<EOF
[Unit]
Description=SSH Chat TCP server
After=network.target

[Service]
Type=simple
${SVC_USER}WorkingDirectory=$PREFIX
EnvironmentFile=-$PREFIX/sshchat.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$PREFIX/venv/bin/python $PREFIX/server.py
Restart=on-failure
TimeoutStopSec=15
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable sshchat.service
  systemctl restart sshchat.service
  echo "info: systemd service sshchat.service enabled and restarted"
elif [[ "$INSTALL_OPENRC" -eq 1 ]] && command -v rc-update &>/dev/null && [[ -d /etc/init.d ]]; then
  OPENRC_SRC="$SCRIPT_DIR/scripts/sshchat.openrc"
  OPENRC_DST=/etc/init.d/sshchat
  if [[ -f "$OPENRC_SRC" ]]; then
    sed -e "s|__SSHCHAT_PREFIX__|$PREFIX|g" -e "s|__SSHCHAT_RUN_USER__|$RUN_USER|g" \
      "$OPENRC_SRC" >"$OPENRC_DST"
  else
    cat >"$OPENRC_DST" <<EOF
#!/sbin/openrc-run
description="SSHChat TCP chat server"
command="$PREFIX/server.sh"
command_user="$RUN_USER"
directory="$PREFIX"
command_background=true
pidfile="/run/sshchat.pid"
output_log="$PREFIX/server.log"
error_log="$PREFIX/server.log"
depend() { need net; }
start_pre() { mkdir -p /run; export PYTHONUNBUFFERED=1; }
EOF
  fi
  chmod 755 "$OPENRC_DST"
  # Ensure service user can write the log / runtime files.
  touch "$PREFIX/server.log" 2>/dev/null || true
  if [[ "$RUN_USER" != "root" ]] && id "$RUN_USER" &>/dev/null; then
    chown "$RUN_USER:$(primary_group_of "$RUN_USER")" "$PREFIX/server.log" 2>/dev/null || true
  fi
  rc-update add sshchat default 2>/dev/null || true
  stop_existing_server "$PREFIX"
  if ! rc-service sshchat restart && ! rc-service sshchat start; then
    echo "warning: OpenRC sshchat failed to start; falling back to nohup" >&2
    nohup "$PREFIX/server.sh" >>"$PREFIX/server.log" 2>&1 &
    echo "info: started $PREFIX/server.sh in background (pid $!)"
  else
    echo "info: OpenRC service sshchat enabled and started"
  fi
  echo "info: server log: $PREFIX/server.log"
else
  # Non-systemd / non-OpenRC platforms (e.g. macOS): restart a detached server process.
  # stop_existing_server already ran before the file copy; this catches anything
  # that respawned during the install (unlikely, but cheap insurance) and frees
  # the listening port before we start the fresh interpreter.
  stop_existing_server "$PREFIX"
  nohup "$PREFIX/server.sh" >>"$PREFIX/server.log" 2>&1 &
  SERVER_PID=$!
  echo "info: systemd/OpenRC skipped; started $PREFIX/server.sh in background (pid $SERVER_PID)"
  echo "info: server log: $PREFIX/server.log"
fi

if [[ "$USE_CLOUDFLARE" -eq 1 ]]; then
  CF_SETUP="$SCRIPT_DIR/scripts/setup-cloudflared-file-tunnel.sh"
  if [[ -f "$CF_SETUP" ]]; then
    chmod +x "$CF_SETUP" "$SCRIPT_DIR/scripts/sshchat-cloudflared-tunnel.sh" 2>/dev/null || true
    echo "info: refreshing Cloudflare Quick Tunnel (new public URL required every deploy)..."
    if ! "$CF_SETUP" --prefix "$PREFIX" --env-file "$PREFIX/sshchat.env" --port "$FILE_HTTP_PORT" --wait-url 120; then
      echo "error: Cloudflare tunnel did not publish a usable URL; /sendfile will fail until fixed" >&2
      echo "hint: sudo $CF_SETUP --prefix $PREFIX" >&2
      echo "hint: if rate-limited: sudo $SCRIPT_DIR/scripts/start-cloudflared-once.sh" >&2
    fi
    # Re-apply env ownership if the tunnel helper rewrote sshchat.env as root
    if [[ "$CREATE_RUN_USER" -eq 1 && -f "$PREFIX/sshchat.env" ]]; then
      chown "$RUN_USER:$CLIENT_GROUP" "$PREFIX/sshchat.env"
      chmod 640 "$PREFIX/sshchat.env"
    elif is_darwin && [[ -f "$PREFIX/sshchat.env" ]]; then
      chown "root:$CLIENT_GROUP" "$PREFIX/sshchat.env" 2>/dev/null || true
      chmod 640 "$PREFIX/sshchat.env"
    fi
    if [[ -d "$FILE_STORAGE_DIR" && "$CREATE_RUN_USER" -eq 1 ]]; then
      chown -R "$RUN_USER:$(primary_group_of "$RUN_USER")" "$FILE_STORAGE_DIR"
      chmod 750 "$FILE_STORAGE_DIR"
    fi
    # Hard check: env host must match the live tunnel file
    if [[ -f /var/lib/sshchat/cloudflared/public_url && -f "$PREFIX/sshchat.env" ]]; then
      CF_HOST=$(sed -n 's|^https://||p' /var/lib/sshchat/cloudflared/public_url | tr -d '[:space:]')
      ENV_HOST=$(grep -E '^SSHCHAT_FILE_PUBLIC_HOST=' "$PREFIX/sshchat.env" | head -1 | cut -d= -f2-)
      if [[ -n "$CF_HOST" && "$ENV_HOST" != "$CF_HOST" ]]; then
        echo "error: PUBLIC_HOST mismatch (env=$ENV_HOST tunnel=$CF_HOST)" >&2
      elif [[ -n "$CF_HOST" ]]; then
        echo "info: /sendfile public host confirmed: https://$CF_HOST"
      fi
    fi
  else
    echo "warning: missing $CF_SETUP; skip Cloudflare tunnel" >&2
  fi
else
  # Opting out: stop a previously installed tunnel so old trycloudflare URLs do not linger.
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files sshchat-cloudflared.service >/dev/null 2>&1; then
    systemctl disable --now sshchat-cloudflared.service 2>/dev/null || true
  fi
  if is_darwin && [[ -f /Library/LaunchDaemons/com.sshchat.cloudflared.plist ]]; then
    launchctl bootout system /Library/LaunchDaemons/com.sshchat.cloudflared.plist 2>/dev/null || true
  fi
  pkill -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true
fi

echo
echo "Install path:     $PREFIX"
echo "Client connects:  $SERVER_IP port $PORT (see $PREFIX/sshchat.env)"
if [[ "$CREATE_RUN_USER" -eq 1 ]] || is_darwin; then
  echo "Chat login group: $CLIENT_GROUP (admin-add-user adds each user to this group)"
fi
echo "Add SSH users:    sudo $PREFIX/admin-add-user.sh <user> <pasted-pubkey-line|key.pub|->"
echo "Add federation:   sudo $PREFIX/admin-add-peer.sh <peer_node_id> <peer_host> <peer-pubkey|file>"
echo "Remove federation: sudo $PREFIX/admin-remove-peer.sh <peer_node_id>"
if [[ "$MIGRATE_KEYS" -eq 1 ]]; then
  echo "authorized_keys:  command= paths normalized to $CHAT_ABS"
else
  echo "authorized_keys:  not modified (--no-migrate-keys)"
fi
echo "Optional: open firewall for TCP $PORT"
echo "GUI bundle:     $CLIENT_BUNDLE_JSON  (host=$CLIENT_SSH_HOST ssh_port=$CLIENT_SSH_PORT)"
if [[ "$USE_CLOUDFLARE" -eq 1 ]]; then
  if [[ -f /var/lib/sshchat/cloudflared/public_url ]]; then
    echo "File URL:       $(cat /var/lib/sshchat/cloudflared/public_url)  (Cloudflare Quick Tunnel)"
  else
    echo "File URL:       Cloudflare tunnel pending (see /var/lib/sshchat/cloudflared/)"
  fi
  echo "Note:           with Cloudflare, no need to open firewall for file port $FILE_HTTP_PORT"
elif [[ "$FILE_TRANSFER_ENABLED" -eq 1 ]]; then
  echo "File port:      $FILE_HTTP_PORT (open firewall if users fetch files from outside)"
fi

# Always show federation identity (needed to peer with other servers).
if [[ -f "$PREFIX/sshchat.env" ]]; then
  # shellcheck disable=SC1091
  . "$PREFIX/sshchat.env"
fi
echo
echo "=== Federation (for admin-add-peer.sh on the other server) ==="
echo "Local node id:  ${SSHCHAT_NODE_ID:-$(hostname -f 2>/dev/null || hostname)}"
if [[ -f "$FED_DIR/id_ed25519.pub" ]]; then
  echo "Federation pubkey ($FED_DIR/id_ed25519.pub):"
  cat "$FED_DIR/id_ed25519.pub"
else
  echo "warning: federation pubkey missing at $FED_DIR/id_ed25519.pub" >&2
fi
echo
if [[ "$BUILD_GUI_PACKAGES" -eq 1 ]]; then
  if [[ -x "$SCRIPT_DIR/scripts/build-gui-packages.sh" ]]; then
    echo "info: running scripts/build-gui-packages.sh ..."
    if ! SSHCHAT_BUNDLE_FILE="$CLIENT_BUNDLE_JSON" "$SCRIPT_DIR/scripts/build-gui-packages.sh"; then
      echo "warning: GUI package build failed; install PyInstaller + tkinter or build on a workstation" >&2
    fi
  else
    echo "warning: $SCRIPT_DIR/scripts/build-gui-packages.sh not found or not executable" >&2
  fi
fi
