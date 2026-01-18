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
  if adb connect "${PHONE_IP}:${ADB_PORT}"; then
    ADB_SERIAL="${ADB_SERIAL:-${PHONE_IP}:${ADB_PORT}}"
  else
    echo "WARN: failed to connect to ${PHONE_IP}:${ADB_PORT}" >&2
    echo "Hint: Android shows two ports: a pairing port and a connect port." >&2
    echo "Use the port shown under \"IP address & port\" (not the pairing port)." >&2
  fi
fi

echo
echo "adb devices -l"
adb devices -l

count="$(adb devices | tail -n +2 | awk 'NF {print $1}' | wc -l | tr -d ' ')"
if [[ "$count" == "0" ]]; then
  echo
  echo "ERROR: no devices detected." >&2
  echo "Set PHONE_IP and ADB_PORT (from Android Wireless debugging), then re-run:" >&2
  echo "  export PHONE_IP=...; export ADB_PORT=...; ./remote-demo/bin/adb_connect.sh" >&2
  exit 2
fi

if [[ -n "$ADB_SERIAL" ]]; then
  if adb -s "$ADB_SERIAL" get-state >/dev/null 2>&1; then
    echo
    echo "OK: connected ($ADB_SERIAL)"
    exit 0
  fi
  echo
  echo "WARN: ADB_SERIAL='$ADB_SERIAL' not reachable; falling back to any connected device." >&2
fi

echo
echo "OK: device detected."
echo "Tip: set ADB_SERIAL to choose one from:"
adb devices | tail -n +2 | awk 'NF {print "  export ADB_SERIAL=\""$1"\""}'
