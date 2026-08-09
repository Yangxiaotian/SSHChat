#!/usr/bin/env bash
# Stop rate-limit storms, wait, then start exactly one Quick Tunnel and keep it.
set -uo pipefail

systemctl stop sshchat-cloudflared.service 2>/dev/null || true
systemctl reset-failed sshchat-cloudflared.service 2>/dev/null || true
pkill -9 -f 'cloudflared tunnel' 2>/dev/null || true

# No auto-restart until we have a healthy tunnel
mkdir -p /etc/systemd/system/sshchat-cloudflared.service.d
cat >/etc/systemd/system/sshchat-cloudflared.service.d/override.conf <<'EOF'
[Service]
Restart=no
EOF
systemctl daemon-reload

WAIT_SEC="${1:-1200}"
echo "[once] cooling down ${WAIT_SEC}s with zero tunnel attempts ($(date))"
sleep "$WAIT_SEC"
echo "[once] cooldown done ($(date)); starting service once"

rm -f /var/lib/sshchat/cloudflared/public_url
systemctl start sshchat-cloudflared.service

for i in $(seq 1 60); do
  if [[ -f /var/lib/sshchat/cloudflared/public_url ]]; then
    echo "[once] PUBLIC_URL=$(cat /var/lib/sshchat/cloudflared/public_url)"
    # Re-enable restart for process crashes only (not for quick create storms)
    cat >/etc/systemd/system/sshchat-cloudflared.service.d/override.conf <<'EOF'
[Service]
Restart=on-failure
RestartSec=600
EOF
    systemctl daemon-reload
    systemctl enable sshchat-cloudflared.service
    grep SSHCHAT_FILE /opt/sshchat/sshchat.env
    systemctl is-active sshchat.service sshchat-cloudflared.service
    PUBLIC=$(cat /var/lib/sshchat/cloudflared/public_url)
    sleep 2
    curl --noproxy '*' -sS -o /dev/null -w 'http=%{http_code}\n' --max-time 30 "$PUBLIC/" || true
    journalctl -u sshchat.service -n 6 --no-pager
    exit 0
  fi
  if journalctl -u sshchat-cloudflared.service -n 8 --no-pager 2>/dev/null | grep -qE '429|1015'; then
    echo "[once] still rate-limited; leaving Restart=no. Try again later:"
    echo "  sudo bash $0 900"
    journalctl -u sshchat-cloudflared.service -n 12 --no-pager
    exit 2
  fi
  sleep 2
done

echo "[once] timed out waiting for URL"
journalctl -u sshchat-cloudflared.service -n 20 --no-pager
exit 1
