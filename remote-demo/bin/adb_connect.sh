#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found. Install Android Platform Tools and ensure adb is on PATH." >&2
  exit 1
fi

ADB_SERIAL="${ADB_SERIAL:-}"
PHONE_IP="${PHONE_IP:-}"
ADB_PORT="${ADB_PORT:-}"

if [[ -n "$PHONE_IP" && -n "$ADB_PORT" ]]; then
  echo "Connecting via adb tcpip: ${PHONE_IP}:${ADB_PORT}"
  adb connect "${PHONE_IP}:${ADB_PORT}" || true
  ADB_SERIAL="${ADB_SERIAL:-${PHONE_IP}:${ADB_PORT}}"
fi

echo
echo "adb devices -l"
adb devices -l

if [[ -n "$ADB_SERIAL" ]]; then
  if ! adb -s "$ADB_SERIAL" get-state >/dev/null 2>&1; then
    echo
    echo "ERROR: ADB_SERIAL='$ADB_SERIAL' not reachable." >&2
    echo "Hint: set PHONE_IP + ADB_PORT, or re-pair in Android: Developer options -> Wireless debugging." >&2
    exit 2
  fi
  echo
  echo "OK: connected ($ADB_SERIAL)"
else
  count="$(adb devices | tail -n +2 | awk 'NF {print $1}' | wc -l | tr -d ' ')"
  if [[ "$count" == "0" ]]; then
    echo
    echo "ERROR: no devices detected." >&2
    echo "Set PHONE_IP and ADB_PORT (from Android Wireless debugging), then re-run:" >&2
    echo "  export PHONE_IP=...; export ADB_PORT=...; ./remote-demo/bin/adb_connect.sh" >&2
    exit 2
  fi
  echo
  echo "OK: device detected (set ADB_SERIAL to pick a specific one)."
fi

