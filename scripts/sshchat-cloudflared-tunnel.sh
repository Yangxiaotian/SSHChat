#!/usr/bin/env bash
# Runtime helper for Cloudflare Quick Tunnel (installed to /usr/local/sbin by setup).
# Environment overrides:
#   SSHCHAT_ENV_FILE, SSHCHAT_FILE_LOCAL_URL, SSHCHAT_PREFIX, SSHCHAT_FILE_HTTP_PORT
set -uo pipefail

ENV_FILE="${SSHCHAT_ENV_FILE:-/opt/sshchat/sshchat.env}"
PREFIX="${SSHCHAT_PREFIX:-/opt/sshchat}"
FILE_HTTP_PORT="${SSHCHAT_FILE_HTTP_PORT:-8443}"
LOCAL_URL="${SSHCHAT_FILE_LOCAL_URL:-http://127.0.0.1:${FILE_HTTP_PORT}}"
STATE_DIR=/var/lib/sshchat/cloudflared
STORAGE_DIR=/var/lib/sshchat/files

mkdir -p "$STATE_DIR" "$STORAGE_DIR"
LOG_FILE="$STATE_DIR/tunnel.log"
URL_FILE="$STATE_DIR/public_url"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

restart_sshchat() {
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

file_stat_owner() {
  stat -c '%u:%g' "$1" 2>/dev/null || stat -f '%u:%g' "$1"
}

file_stat_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

update_public_host() {
  # Split locals: with set -u, `local a=1 b=$a` treats a as unbound.
  local url="$1"
  local host="${url#https://}"
  host="${host%%/*}"
  printf '%s\n' "$url" >"$URL_FILE"
  echo "[sshchat-cloudflared] public URL: $url"
  [[ -f "$ENV_FILE" ]] || { echo "[sshchat-cloudflared] missing env $ENV_FILE"; return 1; }
  local current owner mode
  current=$(grep -E '^SSHCHAT_FILE_PUBLIC_HOST=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
  owner=$(file_stat_owner "$ENV_FILE")
  mode=$(file_stat_mode "$ENV_FILE")
  [[ "$current" == "$host" ]] && { echo "[sshchat-cloudflared] PUBLIC_HOST unchanged"; return 0; }
  local tmp
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
  echo "[sshchat-cloudflared] updated env -> $host"
  restart_sshchat
}

port_ready() {
  if command -v curl >/dev/null 2>&1; then
    curl -s --max-time 1 -o /dev/null "http://127.0.0.1:${FILE_HTTP_PORT}/" && return 0
  fi
  (echo >/dev/tcp/127.0.0.1/"${FILE_HTTP_PORT}") 2>/dev/null
}

for _ in $(seq 1 90); do
  port_ready && break
  sleep 1
done

echo "[sshchat-cloudflared] starting tunnel -> $LOCAL_URL"
rm -f "$URL_FILE"

run_tunnel() {
  if command -v stdbuf >/dev/null 2>&1; then
    stdbuf -oL -eL cloudflared tunnel --no-autoupdate --url "$LOCAL_URL"
  else
    cloudflared tunnel --no-autoupdate --url "$LOCAL_URL"
  fi
}

run_tunnel 2>&1 | tee -a "$LOG_FILE" | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ https://[a-zA-Z0-9-]+\.trycloudflare\.com ]]; then
    if [[ ! -f "$URL_FILE" ]]; then
      update_public_host "${BASH_REMATCH[0]}"
    fi
  fi
done
exit "${PIPESTATUS[0]}"
