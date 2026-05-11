#!/usr/bin/env bash
# Build a single-file SSHChat GUI with embedded client-bundle.json (PyInstaller).
# Run on the target OS (Windows → .exe, macOS → .app, Linux → ELF).
#
# Prerequisites: Python 3 + tkinter, this repo, and a bundle file from deploy:
#   sudo ./deploy.sh --client-ssh-host your.domain --client-ssh-port 22
#   # copies $PREFIX/client-bundle.json to ./dist/client-bundle.json when SCRIPT_DIR is writable
#
# Or:  SSHCHAT_BUNDLE_FILE=/path/to/client-bundle.json ./scripts/build-gui-packages.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-cache"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "error: do not run this script with sudo/root." >&2
  echo "       it creates root-owned files and breaks later non-root builds." >&2
  echo "       if you already did, run:" >&2
  echo "       sudo chown -R \"$(logname 2>/dev/null || echo '<user>')\":\"$(id -gn)\" \"$ROOT/build\" \"$ROOT/dist\" \"$ROOT/dist-packages\"" >&2
  exit 1
fi

BUNDLE="${SSHCHAT_BUNDLE_FILE:-$ROOT/dist/client-bundle.json}"
if [[ ! -f "$BUNDLE" ]]; then
  echo "error: missing bundle: $BUNDLE" >&2
  echo "  Deploy with --client-ssh-host/--client-ssh-port, then copy $PREFIX/client-bundle.json here," >&2
  echo "  or set SSHCHAT_BUNDLE_FILE=...  (see client-bundle.example.json)" >&2
  exit 1
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "error: python3 tkinter missing (e.g. apt install python3-tk)" >&2
  exit 1
fi

SEP=":"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) SEP=";" ;;
esac

# Default: ./build/pack-venv, ./build/pyinstaller, ./dist-packages
# If those dirs are root-owned (e.g. past sudo run), auto-use TMPDIR — no sudo needed.
PACKVENV="$ROOT/build/pack-venv"
PYI_WORKPATH="$ROOT/build/pyinstaller"
ARTIFACT_DIR="${SSHCHAT_ARTIFACT_DIR:-$ROOT/dist-packages}"
_sshchat_rm_tree() {
  local p="$1"
  [[ ! -e "$p" ]] && return 0
  rm -rf "$p" 2>/dev/null
}

RESCUE_BASE="${TMPDIR:-/tmp}/sshchat-gui-build-$(id -u)"
failed=""
for p in "$PACKVENV" "$PYI_WORKPATH" "$ARTIFACT_DIR"; do
  if [[ -e "$p" ]] && ! _sshchat_rm_tree "$p"; then
    failed="$p"
    break
  fi
done

if [[ -n "$failed" ]]; then
  if [[ -n "${SSHCHAT_ARTIFACT_DIR:-}" ]]; then
    echo "error: cannot remove $failed (likely wrong ownership)." >&2
    echo "fix:   sudo chown -R \"$(id -un)\":\"$(id -gn)\" \"$failed\"" >&2
    echo "       (if build/ is root-owned, include \"$ROOT/build\".)" >&2
    exit 1
  fi
  echo "warning: default build/output dirs are not removable (often root-owned from an old sudo run)." >&2
  echo "warning: using $RESCUE_BASE instead (no sudo). To use ./dist-packages again:" >&2
  echo "warning:   sudo chown -R \"$(id -un)\":\"$(id -gn)\" \"$ROOT/build\" \"$ROOT/dist-packages\"" >&2
  PACKVENV="$RESCUE_BASE/pack-venv"
  PYI_WORKPATH="$RESCUE_BASE/pyinstaller"
  ARTIFACT_DIR="$RESCUE_BASE/dist-packages"
  mkdir -p "$RESCUE_BASE"
  for p in "$PACKVENV" "$PYI_WORKPATH" "$ARTIFACT_DIR"; do
    if [[ -e "$p" ]] && ! _sshchat_rm_tree "$p"; then
      echo "error: cannot remove $p" >&2
      exit 1
    fi
  done
fi

python3 -m venv "$PACKVENV"
if [[ -f "$PACKVENV/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$PACKVENV/bin/activate"
elif [[ -f "$PACKVENV/Scripts/activate" ]]; then
  # shellcheck disable=SC1090
  source "$PACKVENV/Scripts/activate"
else
  echo "error: venv activate script not found under $PACKVENV" >&2
  exit 1
fi
pip install -q -r "$ROOT/requirements-gui.txt" -r "$ROOT/requirements-packaging.txt"
mkdir -p "$ARTIFACT_DIR"
PYINST=(
  python -m PyInstaller
  --clean
  --noconfirm
  --noconsole
  --name SSHChat
  --paths "$ROOT"
  --hidden-import sshchat_client_util
  --collect-all paramiko
  --collect-all cryptography
  --distpath "$ARTIFACT_DIR"
  --workpath "$PYI_WORKPATH"
  --add-data "$BUNDLE${SEP}."
)

if [[ "$(uname -s)" == "Darwin" ]]; then
  PYINST+=(--osx-bundle-identifier "chat.ssh.SSHChat")
fi

"${PYINST[@]}" "$ROOT/sshchat_gui.py"

echo
echo "Built under: $ARTIFACT_DIR"
echo "  macOS: open $ARTIFACT_DIR/SSHChat.app"
echo "  Linux: $ARTIFACT_DIR/SSHChat"
echo "  Windows: $ARTIFACT_DIR/SSHChat.exe"
