#!/usr/bin/env bash
# Runtime helper for Cloudflare Quick Tunnel (installed to /usr/local/sbin by setup).
# Environment overrides:
#   SSHCHAT_ENV_FILE, SSHCHAT_FILE_LOCAL_URL, SSHCHAT_PREFIX, SSHCHAT_FILE_HTTP_PORT
#   SSHCHAT_CLOUDFLARED_PROTOCOL  — quic|http2|auto (default: http2; better under Clash TUN)
#   SSHCHAT_CLOUDFLARED_EDGE_IP_VERSION — 4|6|auto (default: 4; avoids IPv6 MITM on some ISPs)
set -uo pipefail

ENV_FILE="${SSHCHAT_ENV_FILE:-/opt/sshchat/sshchat.env}"
PREFIX="${SSHCHAT_PREFIX:-/opt/sshchat}"
FILE_HTTP_PORT="${SSHCHAT_FILE_HTTP_PORT:-8443}"
LOCAL_URL="${SSHCHAT_FILE_LOCAL_URL:-http://127.0.0.1:${FILE_HTTP_PORT}}"
# http2 uses TCP/443 and survives many TUN/UDP filters; override with quic if preferred.
PROTOCOL="${SSHCHAT_CLOUDFLARED_PROTOCOL:-http2}"
# Prefer IPv4 edge: some networks MITM IPv6 to api.trycloudflare.com (fake localhost cert).
EDGE_IP_VERSION="${SSHCHAT_CLOUDFLARED_EDGE_IP_VERSION:-4}"
STATE_DIR=/var/lib/sshchat/cloudflared
STORAGE_DIR=/var/lib/sshchat/files

mkdir -p "$STATE_DIR" "$STORAGE_DIR"
# sshchat (User=sshchat) must write last_canvas_invite_base here; cloudflared (root) writes public_url.
if getent group sshchat >/dev/null 2>&1; then
  chgrp sshchat "$STATE_DIR" 2>/dev/null || true
  chmod 775 "$STATE_DIR" 2>/dev/null || true
fi
LOG_FILE="$STATE_DIR/tunnel.log"
URL_FILE="$STATE_DIR/public_url"
LAST_URL_FILE="$STATE_DIR/last_url"
# Local metrics alone are not enough: Clash TUN fake-ip can keep
# ha_connections>=1 while phones still get a dead trycloudflare hostname.
# Probe the public URL via Clash HTTP proxy when present; never through TUN.
METRICS_ADDR="${SSHCHAT_CLOUDFLARED_METRICS:-127.0.0.1:20241}"
HEALTH_GRACE_SEC="${SSHCHAT_CF_HEALTH_GRACE_SEC:-45}"
HEALTH_INTERVAL_SEC="${SSHCHAT_CF_HEALTH_INTERVAL_SEC:-10}"
HEALTH_FAILS_NEED="${SSHCHAT_CF_HEALTH_FAILS:-2}"
PROBE_PROXY="${SSHCHAT_CF_PROBE_PROXY:-http://127.0.0.1:7897}"
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
    # macOS LaunchDaemon has no TTY; nohup often fails. Prefer launchctl submit / bootstrap.
    if [[ "$(uname -s 2>/dev/null)" == "Darwin" ]]; then
      if [[ -f /Library/LaunchDaemons/com.sshchat.server.plist ]]; then
        launchctl kickstart -k system/com.sshchat.server 2>/dev/null \
          || launchctl bootstrap system /Library/LaunchDaemons/com.sshchat.server.plist 2>/dev/null \
          || true
        sleep 1
        if pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
          return 0
        fi
      fi
      launchctl remove com.sshchat.server 2>/dev/null || true
      if launchctl submit -l com.sshchat.server -o "$PREFIX/server.log" -e "$PREFIX/server.log" -- "$PREFIX/server.sh"; then
        return 0
      fi
      echo "[sshchat-cloudflared] WARN: launchctl submit failed, trying nohup" >&2
    fi
    /usr/bin/nohup "$PREFIX/server.sh" >>"$PREFIX/server.log" 2>&1 </dev/null &
    disown 2>/dev/null || true
    sleep 1
    if ! pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
      echo "[sshchat-cloudflared] WARN: failed to restart $PREFIX/server.sh" >&2
    fi
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
  local url="${1:-}"
  local host="${url#https://}"
  host="${host%%/*}"
  printf '%s\n' "$url" >"$URL_FILE"
  if getent group sshchat >/dev/null 2>&1; then
    chgrp sshchat "$URL_FILE" 2>/dev/null || true
    chmod 664 "$URL_FILE" 2>/dev/null || true
  fi
  echo "[sshchat-cloudflared] public URL: $url"
  [[ -f "$ENV_FILE" ]] || { echo "[sshchat-cloudflared] missing env $ENV_FILE"; return 1; }
  local current owner mode
  current=$(grep -E '^SSHCHAT_FILE_PUBLIC_HOST=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
  owner=$(file_stat_owner "$ENV_FILE")
  mode=$(file_stat_mode "$ENV_FILE")
  if [[ "$current" == "$host" ]]; then
    echo "[sshchat-cloudflared] PUBLIC_HOST already $host"
    # Only start sshchat if it is down. Do not bounce a healthy server on every
    # tunnel helper loop — that was wiping in-progress room games.
    if ! pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
      echo "[sshchat-cloudflared] sshchat not running; starting"
      restart_sshchat
    fi
    return 0
  fi
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
  # Live public_url latch is enough for a running server; bouncing it takes
  # origin :8443 down and makes /piano return 502/1033 through Cloudflare.
  if ! pgrep -f "$PREFIX/server.py" >/dev/null 2>&1; then
    echo "[sshchat-cloudflared] sshchat not running; starting"
    restart_sshchat
  else
    echo "[sshchat-cloudflared] sshchat already running (not bouncing)"
  fi
}

port_ready() {
  if command -v curl >/dev/null 2>&1; then
    curl -s --max-time 1 -o /dev/null "http://127.0.0.1:${FILE_HTTP_PORT}/" && return 0
  fi
  (echo >/dev/tcp/127.0.0.1/"${FILE_HTTP_PORT}") 2>/dev/null
}

network_ready() {
  # Boot often brings up launchd/systemd before WAN/DNS; avoid burning a Quick Tunnel slot.
  if command -v curl >/dev/null 2>&1; then
    curl -s --max-time 3 -o /dev/null https://1.1.1.1 && return 0
    curl -s --max-time 3 -o /dev/null https://cloudflare.com && return 0
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 1.1.1.1 443 2>/dev/null && return 0
  fi
  return 1
}

echo "[sshchat-cloudflared] waiting for origin :${FILE_HTTP_PORT} and WAN..."
for _ in $(seq 1 120); do
  port_ready && network_ready && break
  sleep 1
done
if ! port_ready; then
  echo "[sshchat-cloudflared] WARN: origin :${FILE_HTTP_PORT} not up yet; starting tunnel anyway" >&2
fi
if ! network_ready; then
  echo "[sshchat-cloudflared] WARN: WAN not ready yet; starting tunnel anyway" >&2
fi

echo "[sshchat-cloudflared] starting tunnel -> $LOCAL_URL (protocol=$PROTOCOL edge-ip=$EDGE_IP_VERSION)"
# Drop stale hostname so a half-booted server cannot keep advertising a dead tunnel.
rm -f "$URL_FILE"

run_tunnel() {
  local -a args=(tunnel --no-autoupdate --url "$LOCAL_URL" --metrics "$METRICS_ADDR")
  case "$PROTOCOL" in
    ""|auto) ;;
    quic|http2|http2_quic|quic_http2) args+=(--protocol "$PROTOCOL") ;;
    *) echo "[sshchat-cloudflared] unknown protocol '$PROTOCOL', using cloudflared default" ;;
  esac
  case "$EDGE_IP_VERSION" in
    ""|auto) ;;
    4|6) args+=(--edge-ip-version "$EDGE_IP_VERSION") ;;
    *) echo "[sshchat-cloudflared] unknown edge-ip-version '$EDGE_IP_VERSION', using cloudflared default" ;;
  esac
  if command -v stdbuf >/dev/null 2>&1; then
    stdbuf -oL -eL cloudflared "${args[@]}"
  else
    cloudflared "${args[@]}"
  fi
}

metrics_ha_connections() {
  curl -s --noproxy '*' --max-time 2 "http://${METRICS_ADDR}/metrics" 2>/dev/null \
    | awk '/^cloudflared_tunnel_ha_connections/{print int($NF); found=1; exit} END{if(!found) exit 1}'
}

http_code() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 12 "$@" 2>/dev/null || echo 000
}

probe_ok_code() {
  [[ "$1" =~ ^(200|301|302|303|307|308|401|403|404) ]]
}

origin_ok() {
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 2 \
    "http://127.0.0.1:${FILE_HTTP_PORT}/healthz" 2>/dev/null || echo 000)
  probe_ok_code "$code" && return 0
  code=$(curl -sS -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 2 \
    "http://127.0.0.1:${FILE_HTTP_PORT}/" 2>/dev/null || echo 000)
  probe_ok_code "$code"
}

proxy_usable() {
  local p="${PROBE_PROXY}" code
  [[ -n "$p" ]] || return 1
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 -x "$p" \
    https://1.1.1.1/cdn-cgi/trace 2>/dev/null || echo 000)
  [[ "$code" =~ ^(200|301|302) ]]
}

is_clash_fake_ip_host() {
  local host="$1" ip
  [[ -n "$host" ]] || return 1
  ip=$(dig +short A "$host" 2>/dev/null | awk '/^198\.18\./{print; exit}')
  [[ -n "$ip" ]]
}

# Authoritative when Clash mixed-port works. Direct curl is poisoned by TUN
# fake-ip (TLS SYSCALL to 198.18.x) even though phones on real DNS are fine.
probe_public_url() {
  local url="$1" base host code
  [[ -n "$url" ]] || return 1
  base="${url%/}"
  host="${base#https://}"
  host="${host%%/*}"
  if proxy_usable; then
    code=$(http_code -x "$PROBE_PROXY" "${base}/healthz")
    probe_ok_code "$code" && return 0
    code=$(http_code -x "$PROBE_PROXY" "${base}/")
    probe_ok_code "$code" && return 0
    return 1
  fi
  if is_clash_fake_ip_host "$host"; then
    # Cannot verify from this host; caller should fall back to ha_connections.
    return 2
  fi
  code=$(http_code --noproxy '*' "${base}/healthz")
  probe_ok_code "$code" && return 0
  code=$(http_code --noproxy '*' "${base}/")
  probe_ok_code "$code" && return 0
  return 1
}

read_url_file() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  tr -d '[:space:]' <"$path"
}

# Write last_url always; only advertise public_url after a real probe (or HA
# fallback when Clash fake-ip makes HTTP untestable from this machine).
advertise_if_healthy() {
  local url="${1:-}" rc=0
  [[ -n "$url" ]] || return 1
  printf '%s\n' "$url" >"$LAST_URL_FILE"
  if getent group sshchat >/dev/null 2>&1; then
    chgrp sshchat "$LAST_URL_FILE" 2>/dev/null || true
    chmod 664 "$LAST_URL_FILE" 2>/dev/null || true
  fi
  probe_public_url "$url" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    update_public_host "$url"
    return 0
  fi
  if [[ "$rc" -eq 2 ]]; then
    local n
    n=$(metrics_ha_connections || echo 0)
    if [[ "${n:-0}" -ge 1 ]]; then
      echo "[sshchat-cloudflared] WARN: Clash fake-ip; skip public HTTP probe, advertising $url" >&2
      update_public_host "$url"
      return 0
    fi
  fi
  echo "[sshchat-cloudflared] not advertising yet (probe failed rc=$rc): $url" >&2
  rm -f "$URL_FILE"
  return 1
}

# Drop the latch as soon as the public hostname stops answering so /piano
# cannot keep handing out a dead trycloudflare URL. Kill cloudflared only
# after consecutive failures so a brief edge blip can reconnect in place.
watch_tunnel_health() {
  local fails=0 n url rc
  sleep "$HEALTH_GRACE_SEC"
  while true; do
    sleep "$HEALTH_INTERVAL_SEC"
    n=$(metrics_ha_connections || true)
    url=$(read_url_file "$LAST_URL_FILE" 2>/dev/null || read_url_file "$URL_FILE" 2>/dev/null || true)
    if ! origin_ok; then
      echo "[sshchat-cloudflared] origin :${FILE_HTTP_PORT} down; keep tunnel, drop latch" >&2
      rm -f "$URL_FILE"
      continue
    fi
    rc=0
    if [[ -n "$url" ]]; then
      probe_public_url "$url" || rc=$?
    else
      rc=1
    fi
    case "$n" in
      ''|*[!0-9]*) n=0 ;;
    esac
    if [[ "$n" -ge 1 && ( "$rc" -eq 0 || "$rc" -eq 2 ) ]]; then
      fails=0
      if [[ -n "$url" && ! -f "$URL_FILE" ]]; then
        echo "[sshchat-cloudflared] public probe recovered; re-advertise $url" >&2
        advertise_if_healthy "$url" || true
      fi
      continue
    fi
    fails=$((fails + 1))
    echo "[sshchat-cloudflared] tunnel unhealthy ha=$n probe_rc=$rc fails=$fails/$HEALTH_FAILS_NEED url=${url:-none}" >&2
    if [[ "$fails" -ge 1 ]]; then
      rm -f "$URL_FILE"
    fi
    if [[ "$fails" -ge "$HEALTH_FAILS_NEED" ]]; then
      echo "[sshchat-cloudflared] restarting dead Quick Tunnel (stop advertising stale URL)" >&2
      rm -f "$URL_FILE"
      pkill -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true
      return 0
    fi
  done
}

# Quick Tunnel hostnames are random labels; never accept the API host from error logs.
is_quick_tunnel_url() {
  local url="$1" host
  [[ "$url" =~ ^https://[a-zA-Z0-9-]+\.trycloudflare\.com$ ]] || return 1
  host="${url#https://}"
  [[ "$host" != "api.trycloudflare.com" ]] || return 1
  return 0
}

watch_tunnel_health &
WATCH_PID=$!
trap 'kill "$WATCH_PID" 2>/dev/null || true' EXIT

run_tunnel 2>&1 | tee -a "$LOG_FILE" | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ https://[a-zA-Z0-9-]+\.trycloudflare\.com ]]; then
    new_url="${BASH_REMATCH[0]}"
    if ! is_quick_tunnel_url "$new_url"; then
      continue
    fi
    old_url=""
    [[ -f "$URL_FILE" ]] && old_url=$(tr -d '[:space:]' <"$URL_FILE" || true)
    # First sight, hostname change, or latch was dropped after a failed probe.
    if [[ "$old_url" != "$new_url" ]]; then
      advertised=0
      for _try in 1 2 3 4 5 6; do
        if advertise_if_healthy "$new_url"; then
          advertised=1
          break
        fi
        sleep 2
      done
      if [[ "$advertised" -eq 0 ]]; then
        echo "[sshchat-cloudflared] URL minted but not yet publicly reachable; health watcher will retry" >&2
      fi
    fi
  fi
done
exit "${PIPESTATUS[0]}"
