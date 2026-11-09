# Operations and recovery

[Back to README](../README.md) · [API reference](API.md)

Run commands from your project checkout as the user who installed the application, unless a command explicitly uses `sudo`. After a systemd installation, use `current/venv/bin/python` for operator commands; for a temporary local installation, use `venv/bin/python`.

## Publishing safety and recovery

The browser sends stable idempotency keys for post creation and imports. Successful results are stored in the same transaction as the new posts. Retrying the identical request returns its result; reusing its key with changed content returns HTTP 409. If a failed submission might have succeeded, retry the unchanged form first or check the queue before creating a fresh submission. Intentional new submissions of identical text remain allowed.

Each publication has a durable attempt record. Immediately before sending the LinkedIn publication request, the worker records the sending phase. Lost responses, ambiguous server errors, missing result identifiers and interrupted attempts become **uncertain**; they never automatically retry. A worker restart conservatively marks any remaining `posting` records uncertain, including legacy records whose phase is unknown.

For an uncertain post, check your LinkedIn profile, open **Review publication**, acknowledge checking LinkedIn, then either mark it published or return it to a draft. A post URN is optional when marking published. Returning to a draft clears its old schedule. Historical `failed` records and their crash warnings remain untouched; review them before rescheduling.

Known rejected transient failures use bounded exponential backoff and jitter, honor valid `Retry-After` delays up to 24 hours, and stop at `MAX_RETRIES` total attempts. The original scheduled time is preserved separately from the retry time. This reduces duplicate risk; LinkedIn requests and local SQLite updates are not an exactly-once transaction.

Missing, expired or rejected authorization blocks new claims and preserves scheduled posts. Manual pause, authorization blocking and deployment blocking are independent and survive restarts. Once publishing resumes, posts older than `MISSED_AFTER_HOURS` (default 24) become missed; eligible overdue posts may publish immediately. Renewing authorization alone never clears manual pause.

## Monitoring and email

The independent monitoring timer runs every five minutes after an initial boot delay. It checks stale heartbeat, authorization, overdue content, failed/uncertain posts, backup freshness/failure, and less than 1 GiB of free disk space. It does not depend on an open browser or a functioning worker.

Use Resend for alert emails:

1. [Add and verify your sending domain in Resend](https://resend.com/docs/dashboard/domains/introduction), completing its DNS instructions.
2. Create a Resend API key with sending permission for that domain.
3. Edit the project-level `.env` locally; never paste the API key into chat or commit it:

```dotenv
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_your_private_key
RESEND_FROM="Postdesk <alerts@your-verified-domain.com>"
ALERT_TO=you@example.com
```

`ALERT_TO` is one recipient email address. `RESEND_FROM` must use a domain authorized in your Resend account. The API key stays on the server and is never returned by the dashboard.

Install or upgrade with `sudo bash deploy/install.sh`. For later changes to `.env` only, restart the dashboard and worker; the independent monitor reads updated settings on its next run. The System page shows **Resend configured** once all fields are present; this checks presence, not whether the credentials or domain are accepted by Resend.

Check settings without sending email, then explicitly send yourself one test notification:

```bash
current/venv/bin/python -m app.alerts status
current/venv/bin/python -m app.alerts test
```

Run from the project folder. Before installation, use `venv/bin/python` instead. The `test` command sends only its newly queued test alert, not the entire backlog. Check the System page and your inbox/spam folder. A provider acceptance receipt does not prove inbox delivery; this local application does not expose a public delivery-webhook endpoint.

Incident changes and recoveries enter a persistent outbox. Unchanged incidents do not create repeated emails. Failed delivery remains pending with visible sanitized errors and backoff from five minutes to six hours; valid Resend `Retry-After` delays can extend this to 24 hours. A leased delivery is retried after an interrupted monitor process. Each alert uses a stable, payload-specific idempotency key: Resend deduplicates matching retries for its documented 24-hour window. An uncertain delivery retried after that window can still generate a duplicate email. Credentials, sender/recipient changes or a database restore can also affect delivery behavior.

The System page says **Email alerts not configured** and lists missing variable names when settings are incomplete. Pending alerts can deliver once configuration is complete. API failures never fall back silently to another provider. SMTP remains available for older installations: choose `EMAIL_PROVIDER=smtp` and set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_TO`; it uses certificate-verified STARTTLS.

No provider can deliver during a full machine, disk or network outage, and the same computer cannot report while powered off. See [Resend's send-email API](https://resend.com/docs/api-reference/emails/send-email) and [idempotency documentation](https://resend.com/docs/dashboard/emails/idempotency-keys).

Inspect independent jobs:

```bash
journalctl -u autoposter-monitor -n 50 --no-pager
journalctl -u autoposter-backup -n 50 --no-pager
sudo systemctl start autoposter-monitor.service
```

## Backups and restoration

The daily timer runs around 03:00 with a small randomized delay and catches up after downtime. To make a backup now:

```bash
bash deploy/backup.sh
```

Each completed `backups/snapshot-*` directory contains a consistent `posts.db`, a media archive of referenced images and a manifest with SHA-256 checksums. Incomplete snapshots never replace completed ones; successful snapshots older than 14 days are pruned. Backup failures appear in System and monitoring. Files and directories use private permissions.

**These backups are local. Loss of this computer can also destroy them.** Copy completed snapshots to a protected off-device location yourself. `.env`, `data/token.json`, and `data/session.key` are excluded: preserve them separately in secure storage or reauthorize LinkedIn after recovery. The database contains post content and operational history; treat backups as private.

Verify or restore into an empty, isolated directory:

```bash
current/venv/bin/python -m app.backup verify --snapshot backups/snapshot-EXAMPLE
current/venv/bin/python -m app.backup restore --snapshot backups/snapshot-EXAMPLE --destination /tmp/postdesk-restore
```

The restore verifies checksums and SQLite integrity, rejects unsafe archive paths, revokes restored sessions and leaves publishing manually paused. Inspect the restored posts/images before installing them. Stop all services and timers before replacing live database/media files:

```bash
sudo systemctl stop autoposter-worker autoposter-dashboard autoposter-backup.timer autoposter-monitor.timer autoposter-backup.service autoposter-monitor.service
```

Preserve the existing live data as a separate recovery copy. Move the restored `posts.db` to your configured `DB_PATH` (default `data/posts.db`), ensure no stale `-wal`/`-shm` files accompany the replaced database, and install restored images into `media/`. Retain existing credentials securely, fix ownership to your normal user, and restart services/timers. Check authorization and review uncertain/failed posts before manually resuming publishing.

## Releases, failed upgrades and rollback

`current` identifies the active immutable source/environment; `previous` retains the prior installed release. Runtime data remains in the project-level `data/`, `media/`, `logs/` and `backups/` directories. Keep the project directory in place.

```bash
sudo bash deploy/install.sh
# Restore the previous installed source/environment:
sudo bash deploy/rollback.sh
```

A failed upgrade restores the previous release when one exists and leaves publishing paused. Migrations are additive; rollback retains the current database and audit history rather than silently discarding new records. Pre-migration database/media copies live in `deployment-backups/`. On the first managed installation there is no prior managed release: failure leaves publishing stopped/paused and the snapshot available for manual recovery. Do not delete failed releases or snapshots until recovery is complete.

After rollback or fixing a deployment failure, verify health and clear the deployment block as your normal user, from the project folder:

```bash
current/venv/bin/python -m app.manage recover-deployment
```

This verifies readiness and leaves manual pause enabled. Review the queue in the dashboard, validate authorization if needed, and manually resume. If the first installation failed before `current` existed, fix the reported problem and rerun the installer instead. Operator tools never run shell commands received from the browser.

## Routine operation

```bash
# Logs; Ctrl+C exits the viewer only
journalctl -u autoposter-worker -f
# Apply .env changes
sudo systemctl restart autoposter-worker autoposter-dashboard
# Stop/start publishing process
sudo systemctl stop autoposter-worker
sudo systemctl start autoposter-worker
# Disable unattended operation
sudo systemctl disable --now autoposter-worker autoposter-dashboard autoposter-backup.timer autoposter-monitor.timer
# Remove units, preserving data and releases
sudo bash deploy/uninstall.sh
```

The worker has an OS lifetime lock keyed to the database, rotating application logs, and graceful shutdown. `/healthz` checks the dashboard/database only; the authenticated overview reports worker heartbeat. Monitor status reports timer execution. Restarting services does not clear pause or authorization blocks.

## Security and access

This is a single-owner workspace, not a multi-user service. Signed cookies contain a random session identifier, while only its hash is stored in SQLite. Logout revokes that identifier. Password changes invalidate sessions after the new password takes effect on restart; login throttling survives server restarts. Cookies are HttpOnly and SameSite Strict, mutations require CSRF tokens, and security headers restrict content and framing.

The server binds to `127.0.0.1` only. For remote Linux access, use an SSH tunnel:

```bash
ssh -L 8501:127.0.0.1:8501 user@your-server
```

For future domain access, configure nginx using `deploy/nginx.conf.example`, obtain a TLS certificate and set `COOKIE_SECURE=true` before using HTTPS. Leave it false for local HTTP/SSH tunnels. Do not expose public HTTP. OAuth secrets are never sent to the browser. Logs, audit details and backups may contain post content.

LinkedIn app setup: create an app at https://www.linkedin.com/developers/apps, enable member posting and OpenID Connect sign-in products, register `http://localhost:8000/callback`, and set client ID/secret in `.env`. Posting uses official member APIs; company-page posting is not implemented. Maintain the configured `LINKEDIN_VERSION` as LinkedIn retires versions. Authorization is not permanent; renew when warned.
