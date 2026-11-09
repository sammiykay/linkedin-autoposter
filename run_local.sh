#!/usr/bin/env bash
# Quick local run (no systemd). Starts worker in background and dashboard in foreground.
set -e
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Edit .env first, then re-run."; exit 0; }
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
./venv/bin/python -m app.worker &
WORKER=$!
trap "kill $WORKER" EXIT
./venv/bin/python -m app.dashboard
