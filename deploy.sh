#!/usr/bin/env bash
# One-shot install: copy app under PREFIX, venv + prompt_toolkit, sshchat.env, systemd unit.
# Linux: systemd + service user. macOS: auto local-dev (no useradd/groupadd/systemd).
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

is_darwin() {
  [[ "$(uname -s)" == "Darwin" ]]
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
  --run-user NAME    User to run the server as (default: $RUN_USER)
  --no-run-user      Do not create user; install as root (manual server only)
  --client-ssh-host HOST  Hostname/IP for end-user ssh / GUI installers (default: --server-ip if not loopback, else auto-detect)
  --file-domain DOMAIN    Domain name for file transfer HTTPS (enables Let's Encrypt; default: none, uses self-signed)
  --file-port N      HTTP/HTTPS port for file transfers (default: $FILE_HTTP_PORT)
  --no-file-https    Disable HTTPS for file transfers (use HTTP only)
  --no-file-transfer Disable file transfer feature entirely
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
  chown "$u:$CLIENT_GROUP" "$PREFIX"
  chmod 750 "$PREFIX"

  chown "$u:$CLIENT_GROUP" "$PREFIX/chat.sh" "$PREFIX/client.py"
  chmod 750 "$PREFIX/chat.sh"
  chmod 640 "$PREFIX/client.py"
  if [[ -f "$PREFIX/sshchat.env" ]]; then
    chown "$u:$CLIENT_GROUP" "$PREFIX/sshchat.env"
    chmod 640 "$PREFIX/sshchat.env"
  fi

  chown "$u:$u" "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py" "$PREFIX/server.sh"
  chmod 600 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py"
  chmod 700 "$PREFIX/server.sh"
  if [[ -f "$PREFIX/game_ratings.json" ]]; then
    chown "$u:$u" "$PREFIX/game_ratings.json"
    chmod 660 "$PREFIX/game_ratings.json"
  fi
  if [[ -f "$PREFIX/offline_messages.json" ]]; then
    chown "$u:$u" "$PREFIX/offline_messages.json"
    chmod 660 "$PREFIX/offline_messages.json"
  fi

  chown "$ROOT_OWN" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"
  chmod 700 "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"

  chown -R "$u:$CLIENT_GROUP" "$PREFIX/venv"
  chmod -R 'u=rwX,g=rX,o=-' "$PREFIX/venv"
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

  chown "$ROOT_OWN" "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"
  chmod 600 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/file_sharing.py" "$PREFIX/file_http_server.py"
  chmod 700 "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"
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
      # Alpine/BusyBox adduser: -S for system user, -D for no password, -s for shell
      adduser -S -D -s /sbin/nologin "$RUN_USER" 2>/dev/null || adduser -S -D -s /bin/false "$RUN_USER"
      echo "info: created system user $RUN_USER"
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

rm -rf "$PREFIX/venv"
# Use temp space under PREFIX: macOS /private/tmp can be tight; pip unpacks wheels there by default.
DEPLOY_TMP="$PREFIX/.deploy-tmp"
rm -rf "$DEPLOY_TMP"
mkdir -p "$DEPLOY_TMP"
export TMPDIR="$DEPLOY_TMP"
export PIP_NO_CACHE_DIR=1

python3 -m venv "$PREFIX/venv"

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
pip_run_with_retry "$PREFIX/venv/bin/pip" install -q "${PIP_COMMON_ARGS[@]}" -r "$SCRIPT_DIR/requirements-server.txt"
rm -rf "$DEPLOY_TMP"

umask 022
if [[ "$KEEP_ENV" -eq 1 && -f "$PREFIX/sshchat.env" ]]; then
  echo "info: keeping existing $PREFIX/sshchat.env (--keep-env)"
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

FED_DIR="$PREFIX/federation"
mkdir -p "$FED_DIR"
chmod 700 "$FED_DIR"
if [[ ! -f "$FED_DIR/id_ed25519" ]]; then
  ssh-keygen -t ed25519 -f "$FED_DIR/id_ed25519" -N "" -C "sshchat-federation@$(hostname -f 2>/dev/null || hostname)" >/dev/null
  echo "info: generated federation key $FED_DIR/id_ed25519.pub"
fi
if [[ "$CREATE_RUN_USER" -eq 1 ]] && ! is_darwin; then
  FED_USER=sshchat-federation
  if ! id "$FED_USER" &>/dev/null; then
    # Prefer useradd (standard Linux), fall back to adduser (Alpine/BusyBox)
    if command -v useradd &>/dev/null; then
      useradd -r -s /usr/sbin/nologin -d "$FED_DIR" -c "SSHChat federation" "$FED_USER" 2>/dev/null || true
    elif command -v adduser &>/dev/null; then
      adduser -S -D -h "$FED_DIR" -s /sbin/nologin -g "SSHChat federation" "$FED_USER" 2>/dev/null || \
        adduser -S -D -h "$FED_DIR" -s /bin/false "$FED_USER" 2>/dev/null || true
    fi
    if id "$FED_USER" &>/dev/null; then
      echo "info: created federation user $FED_USER (for inbound peer SSH)"
    fi
  fi
fi
if [[ "$RUN_USER" != "root" ]]; then
  chown -R "$RUN_USER:$RUN_USER" "$FED_DIR"
  chmod 600 "$FED_DIR/id_ed25519" 2>/dev/null || true
  chmod 644 "$FED_DIR/id_ed25519.pub" 2>/dev/null || true
fi

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
  chmod 755 "$PREFIX/chat.sh" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh" "$PREFIX/admin-add-peer.sh" "$PREFIX/admin-remove-peer.sh" "$PREFIX/federation-bridge.sh"
  chmod 644 "$PREFIX/server.py" "$PREFIX/games.py" "$PREFIX/ratings.py" "$PREFIX/sgs_data.py" "$PREFIX/library.py" "$PREFIX/dict_lookup.py" "$PREFIX/session_store.py" "$PREFIX/federation.py" "$PREFIX/offline_messages.py" "$PREFIX/client.py"
  [[ -f "$PREFIX/sshchat.env" ]] && chmod 644 "$PREFIX/sshchat.env"
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
else
  # Non-systemd platforms (e.g. macOS): restart a detached server process.
  # stop_existing_server already ran before the file copy; this catches anything
  # that respawned during the install (unlikely, but cheap insurance) and frees
  # the listening port before we start the fresh interpreter.
  stop_existing_server "$PREFIX"
  nohup "$PREFIX/server.sh" >>"$PREFIX/server.log" 2>&1 &
  SERVER_PID=$!
  echo "info: systemd skipped; started $PREFIX/server.sh in background (pid $SERVER_PID)"
  echo "info: server log: $PREFIX/server.log"
fi

echo
echo "Install path:     $PREFIX"
echo "Client connects:  $SERVER_IP port $PORT (see $PREFIX/sshchat.env)"
if [[ "$CREATE_RUN_USER" -eq 1 ]] || is_darwin; then
  echo "Chat login group: $CLIENT_GROUP (admin-add-user adds each user to this group)"
fi
echo "Add SSH users:    sudo $PREFIX/admin-add-user.sh <user> <pasted-pubkey-line|key.pub|->"
if [[ "$MIGRATE_KEYS" -eq 1 ]]; then
  echo "authorized_keys:  command= paths normalized to $CHAT_ABS"
else
  echo "authorized_keys:  not modified (--no-migrate-keys)"
fi
echo "Optional: open firewall for TCP $PORT"
echo "GUI bundle:     $CLIENT_BUNDLE_JSON  (host=$CLIENT_SSH_HOST ssh_port=$CLIENT_SSH_PORT)"
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
