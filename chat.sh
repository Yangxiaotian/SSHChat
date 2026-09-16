#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$DIR/sshchat.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$DIR/sshchat.env"
  set +a
fi
PY="$DIR/venv/bin/python"
[[ -x "$PY" ]] || PY=python3

# sshd sets SSH_TTY only when the SSH *client* requested a remote PTY.
# ``ssh host free`` without -t leaves SSH_TTY empty: local ssh stays in cooked
# mode so Tab never reaches the server. A server-side pty.spawn cannot fix that.
if [[ -z "${SSH_TTY:-}" ]]; then
  echo "[*] No SSH TTY (remote command without -t). Tab completion will not work."
  echo "[*] Use:  ssh -t ${USER:-user}@host"
  echo "[*] Or:   ssh ${USER:-user}@host          (no trailing command)"
  echo "[*] Example: ssh -t yxt@localhost"
fi

# If stdin/stdout are not TTYs, wrap in a PTY so client.py sees a terminal
# (helps /cls CSI and prompt_toolkit when a TTY *was* requested but fds differ).
run_client() {
  if [[ -t 0 && -t 1 ]]; then
    "$PY" "$DIR/client.py"
    return $?
  fi
  # Prefer a real terminal type; dumb breaks prompt_toolkit.
  if [[ -z "${TERM:-}" || "${TERM}" == "dumb" || "${TERM}" == "unknown" ]]; then
    export TERM=xterm-256color
  fi
  "$PY" -c '
import os, pty, sys
status = pty.spawn([sys.argv[1], sys.argv[2]])
raise SystemExit(os.waitstatus_to_exitcode(status))
' "$PY" "$DIR/client.py"
}

while true; do
  run_client
  rc=$?
  if [[ "$rc" -eq 75 ]]; then
    echo "[INFO] reconnecting in 1s ..."
    sleep 1
    continue
  fi
  exit "$rc"
done
