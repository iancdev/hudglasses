#!/usr/bin/env bash
set -euo pipefail

ANDROID_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JDK_DIR="${ANDROID_DIR}/.jdk"
JAVA_HOME_FILE="${JDK_DIR}/JAVA_HOME"

ensure_jdk17() {
  if [[ -f "${JAVA_HOME_FILE}" ]]; then
    local existing
    existing="$(cat "${JAVA_HOME_FILE}")"
    if [[ -x "${existing}/bin/java" ]]; then
      echo "${existing}"
      return 0
    fi
  fi

  mkdir -p "${JDK_DIR}"

  local os arch url tmp
  os="$(uname -s)"
  arch="$(uname -m)"

  case "${os}" in
  Darwin) os="mac" ;;
  Linux) os="linux" ;;
  *)
    echo "Unsupported OS: ${os}" >&2
    exit 2
    ;;
  esac

  case "${arch}" in
  arm64 | aarch64) arch="aarch64" ;;
  x86_64) arch="x64" ;;
  *)
    echo "Unsupported arch: ${arch}" >&2
    exit 2
    ;;
  esac

  url="https://api.adoptium.net/v3/binary/latest/17/ga/${os}/${arch}/jdk/hotspot/normal/eclipse"
  tmp="${JDK_DIR}/temurin17.tar.gz"

  echo "Downloading JDK 17 (Temurin)..." >&2
  curl -L -o "${tmp}" "${url}"

  echo "Extracting JDK 17..." >&2
  tar -xf "${tmp}" -C "${JDK_DIR}"
  rm -f "${tmp}"

  local candidate java_home
  candidate="$(ls -d "${JDK_DIR}"/jdk-17* 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -z "${candidate}" ]]; then
    echo "Failed to locate extracted JDK in ${JDK_DIR}" >&2
    exit 2
  fi

  if [[ -d "${candidate}/Contents/Home" ]]; then
    java_home="${candidate}/Contents/Home"
  else
    java_home="${candidate}"
  fi

  if [[ ! -x "${java_home}/bin/java" ]]; then
    echo "JDK 17 install missing bin/java at ${java_home}" >&2
    exit 2
  fi

  echo "${java_home}" >"${JAVA_HOME_FILE}"
  echo "${java_home}"
}

JAVA_HOME="$(ensure_jdk17)"
export JAVA_HOME
export PATH="${JAVA_HOME}/bin:${PATH}"

exec "${ANDROID_DIR}/gradlew" "$@"
