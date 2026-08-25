#!/usr/bin/env bash
# Build SSHChat iOS self-test artifact (Simulator .app zip, or device .ipa if signed).
#
# Prerequisites:
#   - Full Xcode (not just Command Line Tools): xcode-select -p → …/Xcode.app/…
#   - xcodegen (brew install xcodegen)
#   - First run: ios/setup.sh (clones/patches Citadel)
#
# Usage:
#   ./scripts/build-ios.sh              # Simulator app → Desktop/SSHChat-stdlib-ios-sim.zip
#   ./scripts/build-ios.sh device       # Needs Apple ID / team in Xcode; produces .ipa if possible
#   SSHCHAT_ARTIFACT_DIR=/path ./scripts/build-ios.sh
#
# Free Apple ID: open ios/SSHChat.xcodeproj, set your Team, Run on your iPhone (7-day resign).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IOS_DIR="$ROOT/ios"
MODE="${1:-sim}"
ARTIFACT_DIR="${SSHCHAT_ARTIFACT_DIR:-$HOME/Desktop}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "error: do not run this script with sudo/root." >&2
  exit 1
fi

if [[ ! -d "$IOS_DIR" ]]; then
  echo "error: missing ios project at $IOS_DIR" >&2
  exit 1
fi

DEVELOPER_DIR="$(xcode-select -p 2>/dev/null || true)"
if [[ -z "$DEVELOPER_DIR" || "$DEVELOPER_DIR" == "/Library/Developer/CommandLineTools" ]]; then
  if [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
    sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
    DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
  else
    echo "error: full Xcode required (Command Line Tools alone cannot build iOS apps)." >&2
    echo "  Install from App Store, then: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" >&2
    exit 1
  fi
fi

if ! command -v xcodegen >/dev/null; then
  echo "error: xcodegen missing. brew install xcodegen" >&2
  exit 1
fi

if [[ ! -d "$IOS_DIR/Packages/Citadel/.git" ]]; then
  "$IOS_DIR/setup.sh"
else
  (cd "$IOS_DIR" && xcodegen generate)
fi

cd "$IOS_DIR"
mkdir -p "$ARTIFACT_DIR"
BUILD_DIR="$IOS_DIR/build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

case "$MODE" in
  sim|simulator)
    DEST="$ARTIFACT_DIR/SSHChat-stdlib-ios-sim.zip"
    xcodebuild \
      -project SSHChat.xcodeproj \
      -scheme SSHChat \
      -configuration Debug \
      -sdk iphonesimulator \
      -derivedDataPath "$BUILD_DIR/DerivedData" \
      CODE_SIGNING_ALLOWED=NO \
      build
    APP="$(find "$BUILD_DIR/DerivedData/Build/Products" -name 'SSHChat.app' -type d | head -1)"
    if [[ -z "$APP" || ! -d "$APP" ]]; then
      echo "error: simulator .app not produced" >&2
      exit 1
    fi
    rm -f "$DEST"
    (cd "$(dirname "$APP")" && zip -qry "$DEST" "$(basename "$APP")")
    ls -lh "$DEST"
    echo "ok: $DEST"
    echo "Install to Simulator: unzip, then xcrun simctl install booted SSHChat.app"
    ;;
  device|ipa)
    DEST="$ARTIFACT_DIR/SSHChat-stdlib.ipa"
    # Automatic signing needs a Development Team set in the project / env.
    TEAM="${DEVELOPMENT_TEAM:-${IOS_DEVELOPMENT_TEAM:-6Q9L8CDXSY}}"
    EXTRA=()
    if [[ -n "$TEAM" ]]; then
      EXTRA+=(DEVELOPMENT_TEAM="$TEAM")
    fi
    xcodebuild \
      -project SSHChat.xcodeproj \
      -scheme SSHChat \
      -configuration Release \
      -sdk iphoneos \
      -derivedDataPath "$BUILD_DIR/DerivedData" \
      -archivePath "$BUILD_DIR/SSHChat.xcarchive" \
      "${EXTRA[@]}" \
      archive
    # Export ad-hoc / development IPA via temporary exportOptions
    EXPORT_PLIST="$BUILD_DIR/exportOptions.plist"
    cat > "$EXPORT_PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key>
  <string>development</string>
  <key>compileBitcode</key>
  <false/>
  <key>signingStyle</key>
  <string>automatic</string>
</dict>
</plist>
EOF
    xcodebuild -exportArchive \
      -archivePath "$BUILD_DIR/SSHChat.xcarchive" \
      -exportPath "$BUILD_DIR/export" \
      -exportOptionsPlist "$EXPORT_PLIST" \
      "${EXTRA[@]}"
    IPA="$(find "$BUILD_DIR/export" -name '*.ipa' | head -1)"
    if [[ -z "$IPA" || ! -f "$IPA" ]]; then
      echo "error: .ipa not produced (set DEVELOPMENT_TEAM or open Xcode → Signing & Capabilities)" >&2
      exit 1
    fi
    cp -f "$IPA" "$DEST"
    ls -lh "$DEST"
    echo "ok: $DEST"
    ;;
  *)
    echo "usage: $0 [sim|device]" >&2
    exit 2
    ;;
esac
