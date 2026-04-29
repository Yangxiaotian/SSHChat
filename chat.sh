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
while true; do
  "$PY" "$DIR/client.py"
  rc=$?
  if [[ "$rc" -eq 75 ]]; then
    echo "[INFO] reconnecting in 1s ..."
    sleep 1
    continue
  fi
  exit "$rc"
done
