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
want = "@available(iOS 17.0, watchOS 10.0, macOS 14.0, *)"
for old in (
    "@available(macOS 15.0, *)",
    "@available(iOS 17.0, macOS 14.0, *)",
):
    if old in text:
        text = text.replace(old, want)
if want not in text:
    raise SystemExit("unexpected Citadel TTY.swift availability markers")
p.write_text(text)
print("Citadel TTY availability OK (iOS + watchOS)")

pkg = Path("Packages/Citadel/Package.swift")
pkg_text = pkg.read_text()
if ".watchOS(.v10)" not in pkg_text:
    pkg_text = pkg_text.replace(
        ".iOS(.v17)",
        ".iOS(.v17),\n        .watchOS(.v10)",
    )
    # tolerate already having .iOS(.v17) alone with/without trailing comma forms
    if ".watchOS(.v10)" not in pkg_text:
        pkg_text = pkg_text.replace(
            ".iOS(.v17),",
            ".iOS(.v17),\n        .watchOS(.v10),",
        )
    pkg.write_text(pkg_text)
    print("patched Citadel Package.swift for watchOS")
else:
    print("Citadel Package.swift already lists watchOS")
PY

if ! command -v xcodegen >/dev/null; then
  echo "error: xcodegen missing. brew install xcodegen" >&2
  exit 1
fi
xcodegen generate
echo
echo "Open: $ROOT/SSHChat.xcodeproj"
echo "Schemes: SSHChat (iPhone+Watch) · SSHChatWatch (Watch only)"
echo "Needs watchOS platform in Xcode (Settings → Components) to build."
echo "Self-test build (needs full Xcode):"
echo "  ../scripts/build-ios.sh          # Simulator .app → Desktop zip"
echo "  ../scripts/build-ios.sh device   # .ipa (needs Apple ID team)"
echo "Or in Xcode: select iPhone / Apple Watch Simulator → Run (⌘R)."
