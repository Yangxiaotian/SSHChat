#!/usr/bin/env bash
# One-shot fix: move sshchat-federation home out of /opt/sshchat/federation.
# OpenSSH rejects login when the user's home is owned by another user
# ("bad ownership or modes for directory").
#
#   sudo ./scripts/fix-federation-ssh-home.sh
set -euo pipefail

FED_USER=${SSHCHAT_FEDERATION_USER:-sshchat-federation}
FED_HOME=${SSHCHAT_FEDERATION_HOME:-/var/lib/sshchat-federation}
OLD_HOME=/opt/sshchat/federation
PREFIX=${SSHCHAT_PREFIX:-/opt/sshchat}
SVC_USER=${SSHCHAT_RUN_USER:-sshchat}

[[ ${EUID:-0} -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }
id "$FED_USER" &>/dev/null || { echo "error: user $FED_USER missing" >&2; exit 1; }

mkdir -p "$FED_HOME/.ssh"
cur=$(getent passwd "$FED_USER" | cut -d: -f6)
if [[ -f "$OLD_HOME/.ssh/authorized_keys" ]]; then
  cp -a "$OLD_HOME/.ssh/authorized_keys" "$FED_HOME/.ssh/authorized_keys"
elif [[ -n "$cur" && -f "$cur/.ssh/authorized_keys" && "$cur" != "$FED_HOME" ]]; then
  cp -a "$cur/.ssh/authorized_keys" "$FED_HOME/.ssh/authorized_keys"
fi

chown -R "$FED_USER:$FED_USER" "$FED_HOME"
chmod 755 "$FED_HOME"
chmod 700 "$FED_HOME/.ssh"
[[ -f "$FED_HOME/.ssh/authorized_keys" ]] && chmod 600 "$FED_HOME/.ssh/authorized_keys"

if [[ "$cur" != "$FED_HOME" ]]; then
  usermod -d "$FED_HOME" "$FED_USER"
  echo "info: $FED_USER home $cur -> $FED_HOME"
fi

# Service data stays with the chat service user.
if [[ -d "$OLD_HOME" ]] && id "$SVC_USER" &>/dev/null; then
  chown "$SVC_USER:$SVC_USER" "$OLD_HOME"
  chmod 750 "$OLD_HOME"
  [[ -f "$OLD_HOME/peers.json" ]] && chown "$SVC_USER:$SVC_USER" "$OLD_HOME/peers.json" && chmod 640 "$OLD_HOME/peers.json"
  [[ -f "$OLD_HOME/id_ed25519" ]] && chown "$SVC_USER:$SVC_USER" "$OLD_HOME/id_ed25519" && chmod 600 "$OLD_HOME/id_ed25519"
  [[ -f "$OLD_HOME/id_ed25519.pub" ]] && chown "$SVC_USER:$SVC_USER" "$OLD_HOME/id_ed25519.pub" && chmod 644 "$OLD_HOME/id_ed25519.pub"
fi

chmod 755 "$PREFIX/federation-bridge.sh" 2>/dev/null || true
if getent group sshchat-clients >/dev/null 2>&1; then
  usermod -aG sshchat-clients "$FED_USER" 2>/dev/null || true
fi

if systemctl is-active --quiet sshchat.service 2>/dev/null; then
  systemctl kill -s HUP sshchat.service 2>/dev/null || true
  echo "info: sent SIGHUP to sshchat (peer will retry outbound shortly)"
fi

echo "Done. Watch: journalctl -u ssh -f | grep federation"
echo "Expect: Accepted publickey for sshchat-federation (no more 'bad ownership')"
