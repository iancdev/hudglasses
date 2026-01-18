#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ADB_SERIAL="${ADB_SERIAL:-}"
FPS="${FPS:-60}"
BITRATE="${BITRATE:-16M}"
WINDOW_TITLE="${WINDOW_TITLE:-HUDson}"

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found. Install Android Platform Tools." >&2
  exit 1
fi

if ! command -v scrcpy >/dev/null 2>&1; then
  echo "ERROR: scrcpy not found." >&2
  echo "Install it (macOS): brew install scrcpy" >&2
  exit 1
fi

echo "Starting scrcpy..."
args=(--no-audio --max-fps "$FPS" --bit-rate "$BITRATE" --window-title "$WINDOW_TITLE")
if [[ -n "$ADB_SERIAL" ]]; then
  args+=(--serial "$ADB_SERIAL")
fi

scrcpy "${args[@]}"

