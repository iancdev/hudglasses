#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ADB_SERIAL="${ADB_SERIAL:-}"
FPS="${FPS:-60}"
BITRATE="${BITRATE:-16M}"
WINDOW_TITLE="${WINDOW_TITLE:-HUDson}"
RECORD_PATH="${RECORD_PATH:-remote-demo/out/demo.mkv}"

mkdir -p "$(dirname "$RECORD_PATH")"

if ! command -v scrcpy >/dev/null 2>&1; then
  echo "ERROR: scrcpy not found." >&2
  echo "Install it (macOS): brew install scrcpy" >&2
  exit 1
fi

echo "Recording to: $RECORD_PATH"
args=(--no-audio --max-fps "$FPS" --video-bit-rate "$BITRATE" --window-title "$WINDOW_TITLE" --record "$RECORD_PATH")
if [[ -n "$ADB_SERIAL" ]]; then
  args+=(--serial "$ADB_SERIAL")
fi

scrcpy "${args[@]}"
