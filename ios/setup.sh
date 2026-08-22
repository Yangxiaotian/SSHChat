#!/usr/bin/env bash
# Prepare / refresh the iOS tryout project (Citadel + XcodeGen).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d Packages/Citadel/.git ]]; then
  mkdir -p Packages
  rm -rf Packages/Citadel
  git clone --depth 1 https://github.com/orlandos-nl/Citadel.git Packages/Citadel
fi

python3 <<'PY'
from pathlib import Path
p = Path("Packages/Citadel/Sources/Citadel/TTY/Client/TTY.swift")
text = p.read_text()
old = "@available(macOS 15.0, *)"
new = "@available(iOS 17.0, macOS 14.0, *)"
# Idempotent: also accept already-patched form.
if old in text:
    p.write_text(text.replace(old, new))
    print("patched Citadel TTY availability for iOS")
elif new in text:
    print("Citadel TTY already patched for iOS")
else:
    raise SystemExit("unexpected Citadel TTY.swift availability markers")
PY

if ! command -v xcodegen >/dev/null; then
  echo "error: xcodegen missing. brew install xcodegen" >&2
  exit 1
fi
xcodegen generate
echo
echo "Open: $ROOT/SSHChat.xcodeproj"
echo "Self-test build (needs full Xcode):"
echo "  ../scripts/build-ios.sh          # Simulator .app → Desktop zip"
echo "  ../scripts/build-ios.sh device   # .ipa (needs Apple ID team)"
echo "Or in Xcode: select iPhone Simulator / your device → Run (⌘R)."
