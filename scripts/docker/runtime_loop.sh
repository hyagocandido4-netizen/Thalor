#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${THALOR_REPO_ROOT:-/app}"
CONFIG_PATH="${THALOR_CONFIG:-config/multi_asset.yaml}"
INTERVAL_SEC="${THALOR_RUNTIME_INTERVAL_SEC:-60}"
TOPK="${THALOR_TOPK:-3}"
LOOKBACK_CANDLES="${THALOR_LOOKBACK_CANDLES:-2000}"
RELEASE_ALERTS="${THALOR_RELEASE_ALERTS:-1}"
MAX_FAILURES="${THALOR_MAX_FAILURES:-5}"
FAILURES=0

while true; do
  if python -B -m natbin.runtime_app portfolio observe --repo-root "$REPO_ROOT" --config "$CONFIG_PATH" --once --topk "$TOPK" --lookback-candles "$LOOKBACK_CANDLES" --json; then
    FAILURES=0
    if [ "$RELEASE_ALERTS" = "1" ]; then
      python -B -m natbin.runtime_app alerts release --repo-root "$REPO_ROOT" --config "$CONFIG_PATH" --json || true
    fi
  else
    FAILURES=$((FAILURES + 1))
    echo "[thalor-runtime-loop] observe failed (consecutive_failures=${FAILURES})" >&2
    if [ "$FAILURES" -ge "$MAX_FAILURES" ]; then
      echo "[thalor-runtime-loop] max failures reached; exiting for container restart" >&2
      exit 1
    fi
  fi
  sleep "$INTERVAL_SEC"
done
