#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${THALOR_REPO_ROOT:-/app}"
CONFIG_PATH="${THALOR_CONFIG:-config/multi_asset.yaml}"
INTERVAL_MINUTES="${THALOR_BACKUP_INTERVAL_MINUTES:-60}"
MAX_FAILURES="${THALOR_BACKUP_MAX_FAILURES:-10}"
FAILURES=0

while true; do
  if python -B -m natbin.runtime_app backup --repo-root "$REPO_ROOT" --config "$CONFIG_PATH" --json; then
    FAILURES=0
  else
    FAILURES=$((FAILURES + 1))
    echo "[thalor-backup-loop] backup failed (consecutive_failures=${FAILURES})" >&2
    if [ "$FAILURES" -ge "$MAX_FAILURES" ]; then
      echo "[thalor-backup-loop] max failures reached; exiting for container restart" >&2
      exit 1
    fi
  fi
  sleep "$(( INTERVAL_MINUTES * 60 ))"
done
