#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build_dmg.sh — Build AudioAssist.app and package it as a .dmg
#
# Usage:
#   bash scripts/build_dmg.sh [--skip-swift] [--skip-pyinstaller]
#
# Prerequisites:
#   - Xcode Command Line Tools (swift, codesign)
#   - Python environment with pyinstaller installed
#   - create-dmg (brew install create-dmg) OR hdiutil (built-in fallback)
#
# The script does NOT sign or notarize — do that separately with:
#   codesign --deep --force --options runtime \
#     --entitlements entitlements.plist \
#     --sign "Developer ID Application: ..." \
#     dist/AudioAssist.app
#   xcrun notarytool submit AudioAssist.dmg --wait ...
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_NAME="AudioAssist"
DMG_NAME="${APP_NAME}.dmg"
HELPER_DIR="${ROOT}/native/AudioAssistCaptureHelper"
HELPER_BIN="${HELPER_DIR}/.build/release/AudioAssistCaptureHelper"
DIST_DIR="${ROOT}/dist"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"

SKIP_SWIFT=false
SKIP_PYINSTALLER=false

for arg in "$@"; do
  case "$arg" in
    --skip-swift)        SKIP_SWIFT=true ;;
    --skip-pyinstaller)  SKIP_PYINSTALLER=true ;;
  esac
done

# ── Step 1: Build Swift helper ────────────────────────────────────────────────
if [ "$SKIP_SWIFT" = false ]; then
  echo "==> [1/3] Building Swift helper (release)…"
  (cd "$HELPER_DIR" && swift build -c release)
  echo "    Helper binary: $HELPER_BIN"
else
  echo "==> [1/3] Skipping Swift build (--skip-swift)"
fi

if [ ! -f "$HELPER_BIN" ]; then
  echo "ERROR: Helper binary not found at $HELPER_BIN"
  echo "       Run 'swift build -c release' in $HELPER_DIR first."
  exit 1
fi

# ── Step 2: PyInstaller ───────────────────────────────────────────────────────
if [ "$SKIP_PYINSTALLER" = false ]; then
  echo "==> [2/3] Running PyInstaller…"
  cd "$ROOT"
  python -m PyInstaller AudioAssist.spec --noconfirm
  echo "    App bundle: $APP_PATH"
else
  echo "==> [2/3] Skipping PyInstaller (--skip-pyinstaller)"
fi

if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: App bundle not found at $APP_PATH"
  echo "       Run PyInstaller first."
  exit 1
fi

# ── Step 2.5: Sign helper with required entitlements ─────────────────────────
echo "==> [2.5/3] Signing helper with TCC entitlements…"
HELPER_IN_BUNDLE="${APP_PATH}/Contents/Frameworks/AudioAssistCaptureHelper"
HELPER_ENTITLEMENTS="${ROOT}/entitlements_helper.plist"

if [ ! -f "$HELPER_ENTITLEMENTS" ]; then
  cat > "$HELPER_ENTITLEMENTS" << 'ENTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.device.audio-input</key>
    <true/>
    <key>com.apple.security.device.microphone</key>
    <true/>
    <key>com.apple.security.screen-recording</key>
    <true/>
</dict>
</plist>
ENTEOF
fi

if [ -f "$HELPER_IN_BUNDLE" ]; then
  codesign --force --sign - \
    --entitlements "$HELPER_ENTITLEMENTS" \
    "$HELPER_IN_BUNDLE"
  echo "    Helper signed: $HELPER_IN_BUNDLE"
else
  echo "    WARNING: helper not found in bundle at $HELPER_IN_BUNDLE"
fi

# ── Step 3: Package as DMG ────────────────────────────────────────────────────
echo "==> [3/3] Creating DMG…"

# Remove stale DMG if present
rm -f "$DMG_PATH"

if command -v create-dmg &>/dev/null; then
  # Preferred: create-dmg (prettier, with background + layout)
  create-dmg \
    --volname "${APP_NAME}" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "${APP_NAME}.app" 175 190 \
    --hide-extension "${APP_NAME}.app" \
    --app-drop-link 425 190 \
    "$DMG_PATH" \
    "$APP_PATH"
else
  # Fallback: hdiutil (built-in, no staging folder needed)
  echo "    create-dmg not found; using hdiutil fallback."
  STAGING=$(mktemp -d)
  cp -r "$APP_PATH" "$STAGING/"
  ln -s /Applications "$STAGING/Applications"
  hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    "$DMG_PATH"
  rm -rf "$STAGING"
fi

echo ""
echo "✓ Done."
echo "  DMG: $DMG_PATH"
echo ""
echo "Next steps (optional):"
echo "  1. Sign:       codesign --deep --force --options runtime \\"
echo "                   --entitlements entitlements.plist \\"
echo "                   --sign 'Developer ID Application: ...' \\"
echo "                   $APP_PATH"
echo "  2. Notarize:   xcrun notarytool submit $DMG_PATH --wait"
echo "  3. Staple:     xcrun stapler staple $DMG_PATH"
