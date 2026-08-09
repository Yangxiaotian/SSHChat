#!/usr/bin/env bash
# Install Cloudflare Quick Tunnel for SSHChat file HTTP and keep it running.
# Used by deploy.sh (default) and can be run standalone:
#   sudo ./scripts/setup-cloudflared-file-tunnel.sh
#   sudo ./scripts/setup-cloudflared-file-tunnel.sh --prefix /opt/sshchat --wait-url 90
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HELPER_SRC="$SCRIPT_DIR/sshchat-cloudflared-tunnel.sh"

PREFIX="${SSHCHAT_PREFIX:-/opt/sshchat}"
ENV_FILE=""
FILE_HTTP_PORT="${SSHCHAT_FILE_HTTP_PORT:-8443}"
STATE_DIR=/var/lib/sshchat/cloudflared
STORAGE_DIR=/var/lib/sshchat/files
HELPER=/usr/local/sbin/sshchat-cloudflared-tunnel.sh
UNIT=/etc/systemd/system/sshchat-cloudflared.service
PLIST=/Library/LaunchDaemons/com.sshchat.cloudflared.plist
WAIT_URL_SEC=90
INSTALL_ONLY=0

is_darwin() { [[ "$(uname -s)" == "Darwin" ]]; }

usage() {
  cat >&2 <<EOF
Usage: sudo $0 [options]

Options:
  --prefix DIR       SSHChat install prefix (default: $PREFIX)
  --env-file PATH    Path to sshchat.env (default: PREFIX/sshchat.env)
  --port N           Local file HTTP port (default: $FILE_HTTP_PORT)
  --wait-url SEC     Seconds to wait for trycloudflare URL after start (default: $WAIT_URL_SEC; 0=skip)
  --install-only     Install binary + unit/helper; do not start / wait
  -h, --help         Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX=${2:?}; shift 2 ;;
    --env-file) ENV_FILE=${2:?}; shift 2 ;;
    --port) FILE_HTTP_PORT=${2:?}; shift 2 ;;
    --wait-url) WAIT_URL_SEC=${2:?}; shift 2 ;;
    --install-only) INSTALL_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }
[[ -f "$HELPER_SRC" ]] || { echo "error: missing $HELPER_SRC" >&2; exit 1; }

ENV_FILE="${ENV_FILE:-$PREFIX/sshchat.env}"
LOCAL_URL="http://127.0.0.1:${FILE_HTTP_PORT}"
mkdir -p "$STATE_DIR" "$STORAGE_DIR" /usr/local/sbin /usr/local/bin

install_cloudflared_bin() {
  if command -v cloudflared >/dev/null 2>&1; then
    echo "info: cloudflared already installed: $(command -v cloudflared)"
    cloudflared --version 2>/dev/null || true
    return 0
  fi
  local dest=/usr/local/bin/cloudflared
  if is_darwin; then
    if command -v brew >/dev/null 2>&1; then
      echo "info: installing cloudflared via Homebrew"
      local brew_user
      brew_user=$(stat -f '%Su' /dev/console 2>/dev/null || true)
      if [[ -n "$brew_user" && "$brew_user" != "root" ]]; then
        su - "$brew_user" -c 'brew install cloudflared' || true
      else
        brew install cloudflared || true
      fi
      if command -v cloudflared >/dev/null 2>&1; then
        return 0
      fi
      for p in /usr/local/bin/cloudflared /opt/homebrew/bin/cloudflared; do
        [[ -x "$p" ]] && { [[ "$p" != "$dest" ]] && ln -sf "$p" "$dest"; return 0; }
      done
    fi
    local arch asset url tmp
    arch=$(uname -m)
    asset="cloudflared-darwin-amd64.tgz"
    [[ "$arch" == "arm64" ]] && asset="cloudflared-darwin-arm64.tgz"
    url="https://github.com/cloudflare/cloudflared/releases/latest/download/${asset}"
    echo "info: downloading $url"
    tmp=$(mktemp -d)
    if curl -fsSL "$url" -o "$tmp/cf.tgz"; then
      tar -xzf "$tmp/cf.tgz" -C "$tmp"
      install -m 755 "$tmp/cloudflared" "$dest"
      rm -rf "$tmp"
      return 0
    fi
    rm -rf "$tmp"
  else
    local arch asset url
    arch=$(uname -m)
    case "$arch" in
      x86_64|amd64) asset=cloudflared-linux-amd64 ;;
      aarch64|arm64) asset=cloudflared-linux-arm64 ;;
      armv7l) asset=cloudflared-linux-arm ;;
      *) echo "error: unsupported arch for cloudflared: $arch" >&2; return 1 ;;
    esac
    url="https://github.com/cloudflare/cloudflared/releases/latest/download/${asset}"
    echo "info: downloading $url"
    if curl -fsSL "$url" -o "$dest"; then
      chmod 755 "$dest"
      return 0
    fi
  fi
  echo "error: failed to install cloudflared; install it manually and re-run" >&2
  return 1
}

install_helper() {
  install -m 755 "$HELPER_SRC" "$HELPER"
}

install_linux_unit() {
  cat >"$UNIT" <<EOF
[Unit]
Description=Cloudflare Tunnel for SSHChat file transfer
After=network-online.target sshchat.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=$HELPER
Restart=on-failure
RestartSec=300
Environment=SSHCHAT_ENV_FILE=$ENV_FILE
Environment=SSHCHAT_FILE_LOCAL_URL=$LOCAL_URL
Environment=SSHCHAT_PREFIX=$PREFIX
Environment=SSHCHAT_FILE_HTTP_PORT=$FILE_HTTP_PORT
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

install_macos_daemon() {
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.sshchat.cloudflared</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HELPER</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SSHCHAT_ENV_FILE</key>
    <string>$ENV_FILE</string>
    <key>SSHCHAT_FILE_LOCAL_URL</key>
    <string>$LOCAL_URL</string>
    <key>SSHCHAT_PREFIX</key>
    <string>$PREFIX</string>
    <key>SSHCHAT_FILE_HTTP_PORT</key>
    <string>$FILE_HTTP_PORT</string>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>60</integer>
  <key>StandardOutPath</key>
  <string>$STATE_DIR/launchd.out</string>
  <key>StandardErrorPath</key>
  <string>$STATE_DIR/launchd.err</string>
</dict>
</plist>
EOF
  chmod 644 "$PLIST"
}

stop_existing() {
  if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT" ]]; then
    systemctl stop sshchat-cloudflared.service 2>/dev/null || true
  fi
  if is_darwin && [[ -f "$PLIST" ]]; then
    launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  fi
  # Also clear a user LaunchAgent leftover from earlier ad-hoc setups
  if is_darwin; then
    local user_plist
    for user_plist in /Users/*/Library/LaunchAgents/com.sshchat.cloudflared.plist; do
      [[ -f "$user_plist" ]] || continue
      local u
      u=$(echo "$user_plist" | cut -d/ -f3)
      launchctl asuser "$(id -u "$u" 2>/dev/null)" launchctl unload "$user_plist" 2>/dev/null || true
    done
  fi
  pkill -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true
  sleep 1
}

start_service() {
  if ! is_darwin && command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system || -d /lib/systemd/system ]]; then
    systemctl enable sshchat-cloudflared.service
    systemctl restart sshchat-cloudflared.service
    echo "info: started sshchat-cloudflared.service"
    return 0
  fi
  if is_darwin; then
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
    echo "info: loaded $PLIST"
    return 0
  fi
  echo "warning: no systemd/launchd; starting helper in background" >&2
  nohup "$HELPER" >>"$STATE_DIR/tunnel.log" 2>&1 &
}

wait_for_url() {
  local sec="$1"
  [[ "$sec" -gt 0 ]] || return 0
  echo "info: waiting up to ${sec}s for trycloudflare URL..."
  local i
  for i in $(seq 1 "$sec"); do
    if [[ -f "$STATE_DIR/public_url" ]]; then
      echo "info: PUBLIC_URL=$(cat "$STATE_DIR/public_url")"
      return 0
    fi
    if [[ -f "$STATE_DIR/tunnel.log" ]] && grep -qE '429 Too Many Requests|error code: 1015' "$STATE_DIR/tunnel.log" 2>/dev/null; then
      echo "warning: Cloudflare rate-limited quick tunnels; service will retry (RestartSec=300)" >&2
      echo "hint: later run: sudo $SCRIPT_DIR/start-cloudflared-once.sh" >&2
      return 2
    fi
    sleep 1
  done
  echo "warning: no public URL yet; cloudflared will keep retrying in background" >&2
  return 1
}

install_cloudflared_bin || exit 1
install_helper
stop_existing
if is_darwin; then
  install_macos_daemon
else
  install_linux_unit
fi

if [[ "$INSTALL_ONLY" -eq 1 ]]; then
  echo "info: install-only; not starting tunnel"
  exit 0
fi

start_service
wait_for_url "$WAIT_URL_SEC" || true

if [[ -f "$ENV_FILE" ]]; then
  echo "info: file-transfer env:"
  grep -E '^SSHCHAT_FILE_' "$ENV_FILE" || true
fi
if [[ -f "$STATE_DIR/public_url" ]]; then
  PUBLIC=$(cat "$STATE_DIR/public_url")
  sleep 2
  curl --noproxy '*' -sS -o /dev/null -w "info: probe %{http_code} $PUBLIC/\n" --max-time 20 "$PUBLIC/" || true
fi
echo "info: cloudflared tunnel setup done"
