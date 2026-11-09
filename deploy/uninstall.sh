#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo."; exit 1; fi
systemctl disable --now autoposter-backup.timer autoposter-monitor.timer autoposter-backup.service autoposter-monitor.service autoposter-worker autoposter-dashboard || true
rm -f /etc/systemd/system/autoposter-worker.service /etc/systemd/system/autoposter-dashboard.service
rm -f /etc/systemd/system/autoposter-backup.{service,timer} /etc/systemd/system/autoposter-monitor.{service,timer}
systemctl daemon-reload
echo "Services removed. Your data/ and media/ folders were left untouched."
