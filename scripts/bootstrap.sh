#!/usr/bin/env bash
# Bootstrap AudioAssist's repository-owned Python environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKIP_BROWSER=false

for arg in "$@"; do
  case "$arg" in
    --skip-browser) SKIP_BROWSER=true ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: bash scripts/bootstrap.sh [--skip-browser]" >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required. Install it from https://docs.astral.sh/uv/." >&2
  exit 1
fi

cd "$PROJECT_ROOT"

PLATFORM_EXTRA="other"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  PLATFORM_EXTRA="mac"
fi

uv sync --frozen \
  --extra "$PLATFORM_EXTRA" \
  --extra dev \
  --extra build \
  --reinstall-package audioassist-local

if [ "$SKIP_BROWSER" = false ]; then
  "$PROJECT_ROOT/.venv/bin/python" -m playwright install chromium
fi

echo "AudioAssist environment ready: $PROJECT_ROOT/.venv"
echo "CLI: $PROJECT_ROOT/.venv/bin/audioassist"
