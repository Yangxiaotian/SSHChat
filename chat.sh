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

# Run client.py. When SSH did not allocate a TTY (e.g. ``ssh host free`` without
# -t), wrap in a PTY so prompt_toolkit Tab-complete and /cls keep working.
run_client() {
  if [[ -t 0 && -t 1 ]]; then
    "$PY" "$DIR/client.py"
    return $?
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
