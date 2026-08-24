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
      i386|i686|x86)
        echo "error: cloudflared has no $arch build (common on iSH); use --no-cloudflare / LAN file URLs" >&2
        return 1
        ;;
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
# Boot often races WAN/DNS; retry quickly instead of waiting 5 minutes.
RestartSec=30
Environment=SSHCHAT_ENV_FILE=$ENV_FILE
Environment=SSHCHAT_FILE_LOCAL_URL=$LOCAL_URL
Environment=SSHCHAT_PREFIX=$PREFIX
Environment=SSHCHAT_FILE_HTTP_PORT=$FILE_HTTP_PORT
Environment=SSHCHAT_CLOUDFLARED_PROTOCOL=http2
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

install_macos_server_daemon() {
  # Chat server must come up on boot so the tunnel helper can reach :8443 and
  # then rewrite PUBLIC_HOST. Without this, only cloudflared auto-starts.
  local server_plist=/Library/LaunchDaemons/com.sshchat.server.plist
  cat >"$server_plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.sshchat.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PREFIX/server.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PREFIX</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$PREFIX/server.log</string>
  <key>StandardErrorPath</key>
  <string>$PREFIX/server.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF
  chmod 644 "$server_plist"
  launchctl bootout system "$server_plist" 2>/dev/null || true
  launchctl bootstrap system "$server_plist" 2>/dev/null || launchctl load -w "$server_plist"
  echo "info: installed $server_plist (boot autostart for chat server)"
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
    <key>SSHCHAT_CLOUDFLARED_PROTOCOL</key>
    <string>http2</string>
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
  echo "info: stopping any existing Cloudflare Quick Tunnel (force fresh URL on deploy)"
  if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT" || -f /etc/systemd/system/sshchat-cloudflared.service ]]; then
    systemctl stop sshchat-cloudflared.service 2>/dev/null || true
  fi
  if is_darwin && [[ -f "$PLIST" ]]; then
    launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  fi
  # Remove leftover user LaunchAgents from earlier ad-hoc setups (they mint a
  # second Quick Tunnel and often rewrite sshchat.env without restarting sshchat).
  if is_darwin; then
    local user_plist
    for user_plist in /Users/*/Library/LaunchAgents/com.sshchat.cloudflared.plist; do
      [[ -f "$user_plist" ]] || continue
      local u
      u=$(echo "$user_plist" | cut -d/ -f3)
      launchctl asuser "$(id -u "$u" 2>/dev/null)" launchctl bootout "gui/$(id -u "$u" 2>/dev/null)/com.sshchat.cloudflared" 2>/dev/null || true
      launchctl asuser "$(id -u "$u" 2>/dev/null)" launchctl unload "$user_plist" 2>/dev/null || true
      rm -f "$user_plist"
      echo "info: removed leftover user LaunchAgent $user_plist"
    done
  fi
  # KillMode=process used to leave orphans; always reap them so the next start gets a NEW trycloudflare URL.
  pkill -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true
  pkill -f '/usr/local/sbin/sshchat-cloudflared-tunnel.sh' 2>/dev/null || true
  pkill -f '/Users/.*/var/sshchat-cloudflared/run-tunnel.sh' 2>/dev/null || true
  sleep 1
  pkill -9 -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true
  rm -f "$STATE_DIR/public_url"
}

restart_sshchat_service() {
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files sshchat.service >/dev/null 2>&1; then
    systemctl restart sshchat.service || true
    return 0
  fi
  if pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
    pkill -f "$PREFIX/server.py" || true
    sleep 1
  fi
  if [[ -x "$PREFIX/server.sh" ]]; then
    nohup "$PREFIX/server.sh" >>"$PREFIX/server.log" 2>&1 &
  fi
}

# Always make sshchat.env match the live Quick Tunnel URL (deploy must not leave a stale host).
ensure_env_matches_public_url() {
  local url host current owner mode tmp
  [[ -f "$STATE_DIR/public_url" ]] || return 1
  url=$(tr -d '[:space:]' <"$STATE_DIR/public_url")
  [[ "$url" =~ ^https://[a-zA-Z0-9-]+\.trycloudflare\.com$ ]] || {
    echo "error: invalid public_url: $url" >&2
    return 1
  }
  host="${url#https://}"
  [[ -f "$ENV_FILE" ]] || { echo "error: missing $ENV_FILE" >&2; return 1; }
  current=$(grep -E '^SSHCHAT_FILE_PUBLIC_HOST=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
  owner=$(stat -c '%u:%g' "$ENV_FILE" 2>/dev/null || stat -f '%u:%g' "$ENV_FILE")
  mode=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")
  if [[ "$current" == "$host" ]]; then
    echo "info: sshchat.env PUBLIC_HOST already $host"
    # Still bounce sshchat once so in-memory links match after deploy file swaps.
    restart_sshchat_service
    return 0
  fi
  tmp=$(mktemp)
  grep -vE '^SSHCHAT_FILE_(PUBLIC_HOST|PUBLIC_PORT|USE_HTTPS|HTTP_HOST|HTTP_PORT|TRANSFER_ENABLED|STORAGE_DIR)=' "$ENV_FILE" \
    | grep -v '^# File transfer via Cloudflare Tunnel' >"$tmp" || true
  cat >>"$tmp" <<ENV

# File transfer via Cloudflare Tunnel (managed by sshchat-cloudflared)
SSHCHAT_FILE_TRANSFER_ENABLED=1
SSHCHAT_FILE_HTTP_HOST=127.0.0.1
SSHCHAT_FILE_HTTP_PORT=$FILE_HTTP_PORT
SSHCHAT_FILE_USE_HTTPS=0
SSHCHAT_FILE_PUBLIC_HOST=$host
SSHCHAT_FILE_PUBLIC_PORT=443
SSHCHAT_FILE_STORAGE_DIR=$STORAGE_DIR
ENV
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
  chown "$owner" "$ENV_FILE" 2>/dev/null || true
  chmod "$mode" "$ENV_FILE" 2>/dev/null || true
  echo "info: wrote SSHCHAT_FILE_PUBLIC_HOST=$host into $ENV_FILE"
  restart_sshchat_service
}

# If the helper crashed before writing public_url, recover URL from the fresh log tail.
recover_url_from_log() {
  [[ -f "$STATE_DIR/tunnel.log" ]] || return 1
  local url
  url=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$STATE_DIR/tunnel.log" | tail -1 || true)
  [[ -n "$url" ]] || return 1
  printf '%s\n' "$url" >"$STATE_DIR/public_url"
  echo "info: recovered PUBLIC_URL from tunnel.log -> $url"
}

wait_for_url() {
  local sec="$1"
  local marker="${2:-0}"
  [[ "$sec" -gt 0 ]] || return 0
  echo "info: waiting up to ${sec}s for a NEW trycloudflare URL..."
  local i url
  for i in $(seq 1 "$sec"); do
    # Prefer a public_url file written after this start (mtime/content from live log).
    if [[ -f "$STATE_DIR/tunnel.log" ]]; then
      url=$(tail -c +"$((marker + 1))" "$STATE_DIR/tunnel.log" 2>/dev/null \
        | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -1 || true)
      if [[ -n "$url" ]]; then
        printf '%s\n' "$url" >"$STATE_DIR/public_url"
        echo "info: PUBLIC_URL=$url"
        return 0
      fi
      if tail -c +"$((marker + 1))" "$STATE_DIR/tunnel.log" 2>/dev/null | grep -qE '429 Too Many Requests|error code: 1015'; then
        echo "warning: Cloudflare rate-limited quick tunnels; service will retry (RestartSec=300)" >&2
        echo "hint: later run: sudo $SCRIPT_DIR/start-cloudflared-once.sh" >&2
        return 2
      fi
    fi
    # Helper may have written public_url; only accept if it matches a post-start log URL.
    if [[ -f "$STATE_DIR/public_url" && -f "$STATE_DIR/tunnel.log" ]]; then
      url=$(tr -d '[:space:]' <"$STATE_DIR/public_url")
      if tail -c +"$((marker + 1))" "$STATE_DIR/tunnel.log" 2>/dev/null | grep -qF "$url"; then
        echo "info: PUBLIC_URL=$url"
        return 0
      fi
    fi
    sleep 1
  done
  echo "error: no new public URL after ${sec}s" >&2
  return 1
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

install_cloudflared_bin || exit 1
install_helper
stop_existing
if is_darwin; then
  install_macos_server_daemon
  install_macos_daemon
else
  install_linux_unit
fi

if [[ "$INSTALL_ONLY" -eq 1 ]]; then
  echo "info: install-only; not starting tunnel"
  exit 0
fi

start_service
# Capture log offset AFTER start so we never treat a pre-start URL as "new".
LOG_MARK=$(wc -c <"$STATE_DIR/tunnel.log" 2>/dev/null || echo 0)
rm -f "$STATE_DIR/public_url" 2>/dev/null || true

wait_rc=0
wait_for_url "$WAIT_URL_SEC" "$LOG_MARK" || wait_rc=$?

if [[ -f "$STATE_DIR/public_url" ]]; then
  ensure_env_matches_public_url || wait_rc=1
  PUBLIC=$(cat "$STATE_DIR/public_url")
  sleep 2
  curl --noproxy '*' -sS -o /dev/null -w "info: probe %{http_code} $PUBLIC/\n" --max-time 20 "$PUBLIC/" || true
else
  echo "error: deploy did not obtain a Cloudflare public URL; /sendfile links will break" >&2
  wait_rc=1
fi

if [[ -f "$ENV_FILE" ]]; then
  echo "info: file-transfer env:"
  grep -E '^SSHCHAT_FILE_' "$ENV_FILE" || true
fi
echo "info: cloudflared tunnel setup done"
exit "$wait_rc"
