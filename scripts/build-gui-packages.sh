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

PACKVENV="$ROOT/build/pack-venv"
rm -rf "$PACKVENV"
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

ARTIFACT_DIR="$ROOT/dist-packages"
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
  --workpath "$ROOT/build/pyinstaller"
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
