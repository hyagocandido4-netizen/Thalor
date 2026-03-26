#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${THALOR_REPO_ROOT:-/app}"
CONFIG_PATH="${THALOR_CONFIG:-config/multi_asset.yaml}"
python -B -m natbin.runtime_app healthcheck --repo-root "$REPO_ROOT" --config "$CONFIG_PATH" --json >/tmp/thalor_healthcheck.json
