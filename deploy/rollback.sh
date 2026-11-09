#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec 9>"$APP_DIR/.deploy.lock"
flock -n 9 || { echo 'Another deployment is in progress.'; exit 1; }
cd "$APP_DIR"
export APP_ENV_FILE="$APP_DIR/.env" APP_DATA_DIR="$APP_DIR/data" APP_MEDIA_DIR="$APP_DIR/media" APP_LOG_DIR="$APP_DIR/logs" APP_BACKUP_DIR="$APP_DIR/backups"
"$APP_DIR/current/venv/bin/python" -m app.deployment rollback --root "$APP_DIR"
