#!/usr/bin/env bash
# Build release APK and export to android/app-update/ for server-side OTA distribution.
#
# Outputs:
#   android/app-update/SSHChat-latest.apk      — always overwrite with newest build
#   android/app-update/SSHChat-<versionName>.apk — versioned copy
#   android/app-update/version.json            — versionCode / versionName metadata
#
# Usage:
#   ./scripts/export-android-update.sh
#   SSHCHAT_ANDROID_UPDATE_DIR=/path ./scripts/export-android-update.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GRADLE="$ROOT/android/app/build.gradle.kts"
UPDATE_DIR="${SSHCHAT_ANDROID_UPDATE_DIR:-$ROOT/android/app-update}"

if [[ ! -f "$GRADLE" ]]; then
  echo "error: missing $GRADLE" >&2
  exit 1
fi

version_code="$(sed -n 's/.*versionCode = \([0-9][0-9]*\).*/\1/p' "$GRADLE" | head -1)"
version_name="$(sed -n 's/.*versionName = "\([^"]*\)".*/\1/p' "$GRADLE" | head -1)"

if [[ -z "$version_code" || -z "$version_name" ]]; then
  echo "error: could not read versionCode/versionName from $GRADLE" >&2
  exit 1
fi

mkdir -p "$UPDATE_DIR"

# Build into a temp dir, then install canonical names in UPDATE_DIR.
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sshchat-apk.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

SSHCHAT_ARTIFACT_DIR="$TMP_DIR" "$ROOT/scripts/build-android-apk.sh"

BUILT="$TMP_DIR/SSHChat-stdlib.apk"
if [[ ! -f "$BUILT" ]]; then
  echo "error: build did not produce SSHChat-stdlib.apk" >&2
  exit 1
fi

LATEST="$UPDATE_DIR/SSHChat-latest.apk"
VERSIONED="$UPDATE_DIR/SSHChat-${version_name}.apk"

cp -f "$BUILT" "$LATEST"
cp -f "$BUILT" "$VERSIONED"

built_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
apk_bytes="$(wc -c < "$BUILT" | tr -d ' ')"

cat > "$UPDATE_DIR/version.json" <<EOF
{
  "android": {
    "versionCode": ${version_code},
    "versionName": "${version_name}",
    "apkFile": "SSHChat-latest.apk",
    "builtAt": "${built_at}",
    "sizeBytes": ${apk_bytes}
  }
}
EOF

ls -lh "$LATEST" "$VERSIONED" "$UPDATE_DIR/version.json"
echo "ok: exported android update to $UPDATE_DIR (version ${version_name} / code ${version_code})"
