#!/usr/bin/env bash
# Prepare an isolated release; only switch services after its tests pass.
set -euo pipefail
REBUILD_ARGS=()
case "${1:-}" in
  '') ;;
  --rebuild-env) REBUILD_ARGS=(--rebuild) ;;
  *) echo 'Usage: sudo bash deploy/install.sh [--rebuild-env]'; exit 1 ;;
esac
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"
RUN_GROUP="$(id -gn "$RUN_USER")"
umask 077
[[ "$RUN_USER" != root ]] || { echo 'Run sudo from your normal user account; services must not run as root.'; exit 1; }
if [[ "$APP_DIR" == *[\"\\\%\&\|]* ]]; then
  echo 'Use a project path without quotes, backslashes, %, &, or |.'; exit 1
fi
[[ -f "$APP_DIR/.env" ]] || { install -o "$RUN_USER" -g "$RUN_GROUP" -m 600 "$APP_DIR/.env.example" "$APP_DIR/.env"; echo 'Configure .env, then run the installer again.'; exit 0; }
chmod 600 "$APP_DIR/.env"
chown "$RUN_USER:$RUN_GROUP" "$APP_DIR/.env"
command -v flock >/dev/null || { echo 'Install util-linux (flock) first.'; exit 1; }
exec 9>"$APP_DIR/.deploy.lock"
flock -n 9 || { echo 'Another deployment is in progress.'; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else "Python 3.12 or newer is required")'
mkdir -p "$APP_DIR"/{releases,data,media,logs,backups,deployment-backups}
chown "$RUN_USER:$RUN_GROUP" "$APP_DIR"/{releases,data,media,logs,backups,deployment-backups}
chmod 700 "$APP_DIR"/{data,media,logs,backups,deployment-backups}
RELEASE_DIR="$APP_DIR/releases/$(date +%Y%m%d-%H%M%S)-$RANDOM"
mkdir "$RELEASE_DIR"
cp -a "$APP_DIR/app" "$APP_DIR/tests" "$APP_DIR/deploy" "$APP_DIR/requirements.lock" "$APP_DIR/requirements.txt" "$APP_DIR/README.md" "$RELEASE_DIR/"
chown -R "$RUN_USER:$RUN_GROUP" "$RELEASE_DIR"
sudo -u "$RUN_USER" python3 "$APP_DIR/deploy/prepare_env.py" --root "$APP_DIR" --release "$RELEASE_DIR" "${REBUILD_ARGS[@]}"
export APP_ENV_FILE="$APP_DIR/.env" APP_DATA_DIR="$APP_DIR/data" APP_MEDIA_DIR="$APP_DIR/media" APP_LOG_DIR="$APP_DIR/logs" APP_BACKUP_DIR="$APP_DIR/backups"
"$RELEASE_DIR/venv/bin/python" -c 'import sys; sys.path.insert(0, sys.argv[1]); from app import config; sys.exit(0 if len(config.APP_PASSWORD) >= 12 and config.APP_PASSWORD != "change-me-to-something-long" else "Set a unique APP_PASSWORD of at least 12 characters in .env before activation")' "$RELEASE_DIR"
sudo -u "$RUN_USER" bash -c 'cd "$1" && venv/bin/python -m pytest -q tests' bash "$RELEASE_DIR"
# Stage and validate complete unit files before touching installed units.
UNIT_STAGE="$(mktemp -d)"
trap 'rm -rf "$UNIT_STAGE"' EXIT
for svc in autoposter-worker autoposter-dashboard autoposter-backup autoposter-monitor; do
  sed -e "s|__USER__|$RUN_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" "$APP_DIR/deploy/$svc.service" > "$UNIT_STAGE/$svc.service"
done
cp "$APP_DIR/deploy/"*.timer "$UNIT_STAGE/"
# current may not exist until activation, so verify installed syntax after switch as well.
for unit in "$UNIT_STAGE/"*; do install -m 644 "$unit" "/etc/systemd/system/$(basename "$unit")"; done
systemctl daemon-reload
cd "$APP_DIR"
# Runtime data is always outside releases; OAuth can still run from the project folder.
export APP_ENV_FILE="$APP_DIR/.env" APP_DATA_DIR="$APP_DIR/data" APP_MEDIA_DIR="$APP_DIR/media" APP_LOG_DIR="$APP_DIR/logs" APP_BACKUP_DIR="$APP_DIR/backups"
"$RELEASE_DIR/venv/bin/python" -m app.deployment activate --root "$APP_DIR" --release "$RELEASE_DIR"
# Activation ran as root: hand private runtime artifacts back to the service owner.
chown -R "$RUN_USER:$RUN_GROUP" "$APP_DIR"/{data,logs,backups,deployment-backups}
systemctl enable autoposter-worker autoposter-dashboard
systemctl enable --now autoposter-backup.timer autoposter-monitor.timer
systemctl is-active --quiet autoposter-worker autoposter-dashboard
systemd-analyze verify /etc/systemd/system/autoposter-worker.service /etc/systemd/system/autoposter-dashboard.service
printf '%s\n' 'Ready: http://127.0.0.1:8501' 'Both services are enabled at boot. See README.md for reboot verification and rollback.'
