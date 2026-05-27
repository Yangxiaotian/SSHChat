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
exec "$PY" "$DIR/server.py" "$@"
