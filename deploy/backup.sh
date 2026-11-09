#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
umask 077
PYTHON="$APP_DIR/venv/bin/python"
[[ ! -x "$APP_DIR/current/venv/bin/python" ]] || PYTHON="$APP_DIR/current/venv/bin/python"
"$PYTHON" -m app.backup create
