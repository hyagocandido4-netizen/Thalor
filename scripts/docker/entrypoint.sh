#!/usr/bin/env bash
set -euo pipefail

umask 027
mkdir -p /app/runs /app/data /app/secrets /app/runs/backups /app/runs/logs /app/runs/control /app/runs/reports

exec "$@"
