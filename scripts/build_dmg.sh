#!/usr/bin/env bash
# Build, sign, verify, package, and optionally notarize AudioAssist for macOS.
#
# Usage:
#   bash scripts/build_dmg.sh [--skip-swift] [--skip-pyinstaller]
#
# Environment:
#   AUDIOASSIST_SIGN_IDENTITY  Developer ID Application identity. If unset,
#                              create a local ad-hoc release candidate.
#   AUDIOASSIST_NOTARY_PROFILE notarytool keychain profile. Requires a real
#                               Developer ID identity and enables submit/staple.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${ROOT}/.venv/bin/python"
APP_NAME="AudioAssist"
HELPER_DIR="${ROOT}/native/AudioAssistCaptureHelper"
HELPER_BIN="${HELPER_DIR}/.build/release/AudioAssistCaptureHelper"
DIST_DIR="${ROOT}/dist"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
APP_ENTITLEMENTS="${ROOT}/entitlements.plist"
HELPER_ENTITLEMENTS="${ROOT}/entitlements_helper.plist"
SIGN_IDENTITY="${AUDIOASSIST_SIGN_IDENTITY:--}"
NOTARY_PROFILE="${AUDIOASSIST_NOTARY_PROFILE:-}"
SKIP_SWIFT=false
SKIP_PYINSTALLER=false

for arg in "$@"; do
  case "$arg" in
    --skip-swift) SKIP_SWIFT=true ;;
    --skip-pyinstaller) SKIP_PYINSTALLER=true ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [ ! -x "$PYTHON_BIN" ]; then
  echo "ERROR: Repository Python not found at $PYTHON_BIN" >&2
  echo "       Run 'bash scripts/bootstrap.sh' first." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required to refresh and verify release metadata." >&2
  exit 1
fi
for required in "$APP_ENTITLEMENTS" "$HELPER_ENTITLEMENTS"; do
  if [ ! -f "$required" ]; then
    echo "ERROR: Required release file missing: $required" >&2
    exit 1
  fi
done

APP_VERSION="$(cd "$ROOT" && "$PYTHON_BIN" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"

echo "==> [0/5] Refreshing repository package metadata"
(cd "$ROOT" && uv sync --frozen --extra mac --extra dev --extra build \
  --reinstall-package audioassist-local)
INSTALLED_VERSION="$(cd /tmp && "$PYTHON_BIN" -c 'import importlib.metadata; print(importlib.metadata.version("audioassist-local"))')"
if [ "$INSTALLED_VERSION" != "$APP_VERSION" ]; then
  echo "ERROR: Repository package metadata version $INSTALLED_VERSION does not match $APP_VERSION." >&2
  exit 1
fi
DMG_NAME="${APP_NAME}-${APP_VERSION}.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
VERIFY_PATH="${DIST_DIR}/release-verification-${APP_VERSION}.json"

if [ -n "$NOTARY_PROFILE" ] && [ "$SIGN_IDENTITY" = "-" ]; then
  echo "ERROR: AUDIOASSIST_NOTARY_PROFILE requires AUDIOASSIST_SIGN_IDENTITY." >&2
  exit 1
fi
if [ "$SKIP_PYINSTALLER" = true ] && [ "$SIGN_IDENTITY" != "-" ]; then
  echo "ERROR: Developer ID builds cannot reuse an ad-hoc PyInstaller bundle." >&2
  exit 1
fi

if [ "$SKIP_SWIFT" = false ]; then
  echo "==> [1/5] Building Swift helper (release)"
  (cd "$HELPER_DIR" && swift build -c release)
else
  echo "==> [1/5] Reusing Swift helper (--skip-swift)"
fi
if [ ! -f "$HELPER_BIN" ]; then
  echo "ERROR: Helper binary not found at $HELPER_BIN" >&2
  exit 1
fi

if [ "$SKIP_PYINSTALLER" = false ]; then
  echo "==> [2/5] Building app with locked repository Python"
  cd "$ROOT"
  export PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=1
  export PYINSTALLER_VERIFY_BUNDLE_SIGNATURE=1
  if [ "$SIGN_IDENTITY" = "-" ]; then
    unset AUDIOASSIST_SIGN_IDENTITY
  else
    export AUDIOASSIST_SIGN_IDENTITY="$SIGN_IDENTITY"
  fi
  "$PYTHON_BIN" -m PyInstaller AudioAssist.spec --noconfirm
else
  echo "==> [2/5] Reusing app bundle (--skip-pyinstaller)"
fi
if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: App bundle not found at $APP_PATH" >&2
  exit 1
fi

echo "==> [3/5] Signing nested helper, then outer app"
CODESIGN_ARGS=(--force --sign "$SIGN_IDENTITY")
if [ "$SIGN_IDENTITY" != "-" ]; then
  CODESIGN_ARGS+=(--options runtime --timestamp)
fi
HELPER_IN_BUNDLE="${APP_PATH}/Contents/Frameworks/AudioAssistCaptureHelper"
if [ ! -f "$HELPER_IN_BUNDLE" ]; then
  echo "ERROR: Packaged capture helper missing: $HELPER_IN_BUNDLE" >&2
  exit 1
fi
codesign "${CODESIGN_ARGS[@]}" --entitlements "$HELPER_ENTITLEMENTS" "$HELPER_IN_BUNDLE"
codesign "${CODESIGN_ARGS[@]}" --entitlements "$APP_ENTITLEMENTS" "$APP_PATH"
codesign --verify --all-architectures --deep --strict "$APP_PATH"

echo "==> [4/5] Creating and signing $DMG_NAME"
rm -f "$DMG_PATH"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG_PATH"
codesign "${CODESIGN_ARGS[@]}" "$DMG_PATH"
codesign --verify --verbose=2 "$DMG_PATH"

if [ -n "$NOTARY_PROFILE" ]; then
  echo "==> [4.5/5] Submitting, stapling, and validating notarization"
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
fi

echo "==> [5/5] Verifying release invariants"
VERIFY_ARGS=(
  --app "$APP_PATH"
  --dmg "$DMG_PATH"
  --expected-version "$APP_VERSION"
  --output "$VERIFY_PATH"
)
if [ "$SIGN_IDENTITY" != "-" ]; then
  VERIFY_ARGS+=(--require-developer-id)
fi
if [ -n "$NOTARY_PROFILE" ]; then
  VERIFY_ARGS+=(--require-notarized)
fi
"$PYTHON_BIN" scripts/verify_release.py "${VERIFY_ARGS[@]}"

echo ""
echo "Release candidate complete"
echo "  App:      $APP_PATH"
echo "  DMG:      $DMG_PATH"
echo "  Evidence: $VERIFY_PATH"
if [ "$SIGN_IDENTITY" = "-" ]; then
  echo "  Signing:  ad-hoc (local verification only; not distributable)"
elif [ -z "$NOTARY_PROFILE" ]; then
  echo "  Signing:  Developer ID (notarization not requested)"
else
  echo "  Signing:  Developer ID + notarized/stapled"
fi
