#!/usr/bin/env bash
# Build SSHChat Android release APK and copy it to the Desktop (or SSHCHAT_ARTIFACT_DIR).
#
# Prerequisites:
#   - JDK 17+
#   - Android SDK (ANDROID_HOME or ~/Android/Sdk) with:
#       platforms;android-35  build-tools;35.0.0  platform-tools
#   - Network on first run (Gradle deps)
#
# Usage:
#   ./scripts/build-android-apk.sh
#   SSHCHAT_ARTIFACT_DIR=/path/to/out ./scripts/build-android-apk.sh
#
# Output default: ~/Desktop/SSHChat-stdlib.apk

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ANDROID_DIR="$ROOT/android"
OUT_NAME="SSHChat-stdlib.apk"
ARTIFACT_DIR="${SSHCHAT_ARTIFACT_DIR:-$HOME/Desktop}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "error: do not run this script with sudo/root." >&2
  exit 1
fi

if [[ ! -d "$ANDROID_DIR" ]]; then
  echo "error: missing android project at $ANDROID_DIR" >&2
  exit 1
fi

if ! command -v java >/dev/null 2>&1; then
  echo "error: java not found (need JDK 17+)" >&2
  exit 1
fi

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
if [[ ! -d "$SDK" ]]; then
  echo "error: Android SDK not found at $SDK" >&2
  echo "  set ANDROID_HOME or install the SDK under ~/Android/Sdk" >&2
  exit 1
fi
if [[ ! -f "$SDK/platforms/android-35/android.jar" ]]; then
  echo "error: missing platforms;android-35 under $SDK" >&2
  exit 1
fi
if [[ ! -d "$SDK/build-tools/35.0.0" ]]; then
  echo "error: missing build-tools;35.0.0 under $SDK" >&2
  exit 1
fi

# local.properties is gitignored; always rewrite for this machine
printf 'sdk.dir=%s\n' "$SDK" > "$ANDROID_DIR/local.properties"

export ANDROID_HOME="$SDK"
export JAVA_HOME="${JAVA_HOME:-$(/usr/libexec/java_home 2>/dev/null || true)}"
if [[ -z "${JAVA_HOME:-}" ]]; then
  unset JAVA_HOME
fi

cd "$ANDROID_DIR"
./gradlew assembleRelease --no-daemon

APK="$(find "$ANDROID_DIR/app/build/outputs/apk/release" -name '*.apk' | head -1)"
if [[ -z "$APK" || ! -f "$APK" ]]; then
  echo "error: release APK not produced" >&2
  exit 1
fi

mkdir -p "$ARTIFACT_DIR"
DEST="$ARTIFACT_DIR/$OUT_NAME"
cp -f "$APK" "$DEST"
ls -lh "$DEST"
echo "ok: $DEST"
