#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/bootstrap_env.sh"

REPO_ROOT="${THALOR_REPO_ROOT}"
CONFIG_PATH="${THALOR_CONFIG_PATH}"

exec python -B -m natbin.runtime_app status --repo-root "$REPO_ROOT" --config "$CONFIG_PATH" --json
