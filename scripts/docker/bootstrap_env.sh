#!/usr/bin/env bash
# shellcheck shell=bash
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "scripts/docker/bootstrap_env.sh must be sourced, not executed directly." >&2
  exit 1
fi

_THALOR_DOCKER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_THALOR_DOCKER_REPO_DEFAULT="$(cd "${_THALOR_DOCKER_SCRIPT_DIR}/../.." && pwd)"

export THALOR_REPO_ROOT="${THALOR_REPO_ROOT:-${_THALOR_DOCKER_REPO_DEFAULT}}"

if [[ -n "${THALOR_CONFIG_PATH:-}" ]]; then
  export THALOR_CONFIG="${THALOR_CONFIG:-${THALOR_CONFIG_PATH}}"
else
  export THALOR_CONFIG="${THALOR_CONFIG:-config/multi_asset.yaml}"
  export THALOR_CONFIG_PATH="${THALOR_CONFIG}"
fi

if [[ -n "${THALOR_DASHBOARD_CONFIG_PATH:-}" ]]; then
  export THALOR_DASHBOARD_CONFIG="${THALOR_DASHBOARD_CONFIG:-${THALOR_DASHBOARD_CONFIG_PATH}}"
elif [[ -n "${THALOR_DASHBOARD_CONFIG:-}" ]]; then
  export THALOR_DASHBOARD_CONFIG_PATH="${THALOR_DASHBOARD_CONFIG}"
else
  export THALOR_DASHBOARD_CONFIG="${THALOR_CONFIG}"
  export THALOR_DASHBOARD_CONFIG_PATH="${THALOR_DASHBOARD_CONFIG}"
fi

_thalor_abs_path() {
  local raw="${1:-}"
  if [[ -z "${raw}" ]]; then
    return 0
  fi
  case "${raw}" in
    /*)
      printf '%s' "${raw}"
      ;;
    *)
      printf '%s/%s' "${THALOR_REPO_ROOT%/}" "${raw#./}"
      ;;
  esac
}

_thalor_export_abs_if_set() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "${value}" ]]; then
    return 0
  fi
  export "${name}=$(_thalor_abs_path "${value}")"
}

for _thalor_env_name in \
  THALOR_SECRETS_FILE \
  THALOR__SECURITY__SECRETS_FILE \
  THALOR_CONFIG_PATH \
  THALOR_DASHBOARD_CONFIG_PATH \
  TRANSPORT_ENDPOINT_FILE \
  TRANSPORT_ENDPOINTS_FILE \
  TRANSPORT_LOG_PATH \
  REQUEST_METRICS_LOG_PATH \
  THALOR__NETWORK__TRANSPORT__ENDPOINT_FILE \
  THALOR__NETWORK__TRANSPORT__ENDPOINTS_FILE \
  THALOR__NETWORK__TRANSPORT__STRUCTURED_LOG_PATH \
  THALOR__OBSERVABILITY__REQUEST_METRICS__STRUCTURED_LOG_PATH \
; do
  _thalor_export_abs_if_set "${_thalor_env_name}"
done

export THALOR_CONFIG="${THALOR_CONFIG_PATH}"
export THALOR_DASHBOARD_CONFIG="${THALOR_DASHBOARD_CONFIG_PATH}"

unset _thalor_env_name
