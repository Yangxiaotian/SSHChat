#!/usr/bin/env bash
# One-shot install: copy app under PREFIX, venv + prompt_toolkit, sshchat.env, systemd unit.
# Target: Linux with systemd, python3, useradd.
#
#   sudo ./deploy.sh
#   sudo ./deploy.sh --prefix /Shared --server-ip 10.0.0.5 --port 12345
#   sudo ./deploy.sh --no-systemd

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

PREFIX=/opt/sshchat
SERVER_IP=""
PORT=12345
INSTALL_SYSTEMD=1
RUN_USER=sshchat
CREATE_RUN_USER=1

usage() {
  cat >&2 <<EOF
Usage: sudo $0 [options]

Options:
  --prefix DIR       Install directory (default: $PREFIX)
  --server-ip ADDR   Address clients use to reach this host (default: auto-detect)
  --port N           Listen port (default: $PORT)
  --no-systemd       Do not install or start systemd service
  --run-user NAME    User to run the server as (default: $RUN_USER)
  --no-run-user      Do not create user; install as root (manual server only)
  -h, --help         This help
EOF
}

detect_ip() {
  local ip=""
  if command -v ip &>/dev/null; then
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}' || true)
  fi
  if [[ -z "$ip" ]] && command -v hostname &>/dev/null; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
  fi
  [[ -n "$ip" ]] || ip="127.0.0.1"
  printf '%s' "$ip"
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

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

for f in server.py client.py chat.sh server.sh admin-add-user.sh; do
  [[ -f "$SCRIPT_DIR/$f" ]] || { echo "error: missing $SCRIPT_DIR/$f" >&2; exit 1; }
done

if ! command -v python3 &>/dev/null; then
  echo "error: python3 not found" >&2
  exit 1
fi
if ! python3 -c "import venv" 2>/dev/null; then
  echo "error: python3 venv module missing (e.g. apt install python3-venv)" >&2
  exit 1
fi

[[ -z "$SERVER_IP" ]] && SERVER_IP=$(detect_ip)
if [[ "$SERVER_IP" == "127.0.0.1" ]]; then
  echo "warning: SSHCHAT_SERVER is 127.0.0.1 — remote SSH users must set a reachable IP in $PREFIX/sshchat.env" >&2
fi

mkdir -p "$PREFIX"

if [[ "$CREATE_RUN_USER" -eq 1 ]]; then
  if ! id "$RUN_USER" &>/dev/null; then
    if ! command -v useradd &>/dev/null; then
      echo "error: useradd not found; install shadow-utils or use --no-run-user" >&2
      exit 1
    fi
    useradd -r -s /usr/sbin/nologin "$RUN_USER"
    echo "info: created system user $RUN_USER"
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
cp -f "$SCRIPT_DIR/server.py" "$SCRIPT_DIR/client.py" "$PREFIX/"
cp -f "$SCRIPT_DIR/chat.sh" "$SCRIPT_DIR/server.sh" "$SCRIPT_DIR/admin-add-user.sh" "$PREFIX/"
chmod +x "$PREFIX/chat.sh" "$PREFIX/server.sh" "$PREFIX/admin-add-user.sh"

rm -rf "$PREFIX/venv"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q prompt_toolkit

umask 022
cat >"$PREFIX/sshchat.env" <<EOF
SSHCHAT_SERVER=$SERVER_IP
SSHCHAT_PORT=$PORT
EOF
chmod 0644 "$PREFIX/sshchat.env"

if [[ "$CREATE_RUN_USER" -eq 1 ]]; then
  chown -R "$RUN_USER:$RUN_USER" "$PREFIX"
fi

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

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable sshchat.service
  systemctl restart sshchat.service
  echo "info: systemd service sshchat.service enabled and restarted"
else
  echo "info: systemd skipped; start server with: $PREFIX/server.sh"
fi

echo
echo "Install path:     $PREFIX"
echo "Client connects:  $SERVER_IP port $PORT (see $PREFIX/sshchat.env)"
echo "Add SSH users:    sudo $PREFIX/admin-add-user.sh <user> <key.pub>"
echo "Optional: open firewall for TCP $PORT"
