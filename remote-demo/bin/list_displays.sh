#!/usr/bin/env bash
set -euo pipefail

ADB_SERIAL="${ADB_SERIAL:-}"

if ! command -v scrcpy >/dev/null 2>&1; then
  echo "ERROR: scrcpy not found." >&2
  exit 1
fi

args=(--list-displays)
if [[ -n "$ADB_SERIAL" ]]; then
  args+=(--serial "$ADB_SERIAL")
fi

scrcpy "${args[@]}"

