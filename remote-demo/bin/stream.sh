#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ADB_SERIAL="${ADB_SERIAL:-}"
FPS="${FPS:-60}"
BITRATE="${BITRATE:-16M}"
WINDOW_TITLE="${WINDOW_TITLE:-HUDson}"
DISPLAY_ID="${DISPLAY_ID:-0}"

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
args=(--no-audio --max-fps "$FPS" --video-bit-rate "$BITRATE" --window-title "$WINDOW_TITLE" --display-id "$DISPLAY_ID")
if [[ -z "$ADB_SERIAL" ]]; then
  # Prefer an explicit tcpip device if present (e.g. 192.168.x.y:port), otherwise let scrcpy pick.
  tcp_serial="$(adb devices | tail -n +2 | awk 'NF {print $1}' | grep -E '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+:[0-9]+$' | head -n 1 || true)"
  if [[ -n "$tcp_serial" ]]; then
    ADB_SERIAL="$tcp_serial"
  fi
fi

if [[ -n "$ADB_SERIAL" ]]; then
  args+=(--serial "$ADB_SERIAL")
fi

scrcpy "${args[@]}"
