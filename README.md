# Postdesk / LinkedIn Autoposter

A local, single-owner publishing workspace built with HTML, CSS, vanilla JavaScript, Flask/Waitress, SQLite and systemd. A separate worker publishes while your browser is closed. The computer must be awake and online. Services resume automatically after boot; powered-off or sleeping computers cannot publish.

## Upgrade or activate on this computer

Use Linux with systemd and Python 3.12 or newer. From the project folder:

```bash
cd /home/sammiykay/Downloads/Archives/linkedin-autoposter
# Only if .env is missing:
cp -n .env.example .env
nano .env
sudo bash deploy/install.sh
```

Set a unique `APP_PASSWORD` of at least 12 characters and LinkedIn app credentials in `.env`. Configure Resend to enable email delivery. Existing posts, images, tokens and service names are preserved. Stop any worker launched manually before installing; the application permits only one worker per database.

The installer prepares a new release and virtual environment under `releases/`, installs pinned dependencies, and runs isolated tests before changing running services. It pauses new claims, waits for the current attempt to finish, stops services, takes a pre-migration content snapshot, applies additive migrations, switches `current`, and checks dashboard HTTP readiness plus a fresh worker heartbeat. It restores the previous manual-pause state only after success. Backup and monitoring timers are enabled automatically.

**Installation starts the live worker.** Due posts within the grace window can publish after successful activation if publishing was not paused. To keep publishing paused during an upgrade, pause it in the existing dashboard first.

Open **http://127.0.0.1:8501** and use `APP_PASSWORD` to sign in. Existing sessions from the earlier dashboard must sign in again after this upgrade.

If LinkedIn authorization is needed, run as your normal user from the project folder:

```bash
venv/bin/python -m app.auth
```

On an installed release you can also use `current/venv/bin/python -m app.auth` from this project directory. Authorization opens your browser, saves the token atomically with private permissions, validates the connection and clears only the authorization block. It does not clear a manual pause. The System page also has a **Validate LinkedIn connection** action.

### Verify startup after an actual reboot

After saving other work, reboot the computer yourself. Then check:

```bash
systemctl is-enabled autoposter-worker autoposter-dashboard
systemctl status autoposter-worker autoposter-dashboard --no-pager
systemctl list-timers autoposter-backup.timer autoposter-monitor.timer
curl -f http://127.0.0.1:8501/healthz
```

Both services should be enabled and active; both timers should have a next run. Open the dashboard and check the fresh worker heartbeat, publishing controls and authorization status. A live posting check should be supervised using a post you explicitly intend to publish. Automated tests never publish real posts or reboot your computer.

## Your dashboard

- **Overview:** actual queue counts, upcoming content, worker health, actionable warnings and recent audit events. Pause/resume is persistent; already claimed work finishes.
- **Content queue:** server-side search, 20-post pages and status filters. Desktop table and mobile cards show schedule, image indicator, errors and available actions.
- **Calendar:** month grid and agenda using the server timezone. Click a post to inspect it; uncertain posts open the recovery dialog. The API bounds calendar ranges to 62 days and returns at most 1,000 posts; the interface indicates truncation.
- **Composer:** draft, schedule, or explicitly publish now; attach JPG/PNG/GIF up to 8 MB, add alt text, and see an approximate LinkedIn preview. Maximum 3,000 characters. Published or publishing posts are read-only. Polling preserves unsaved changes and closing an edited form asks before discarding.
- **Activity:** chronological audit history for queue edits, attempts, publishing controls, connection validation and recovery.
- **Import:** CSV/XLSX up to 500 rows and a 20 MB request limit. Every row is checked before the transaction commits. The same file within 24 hours requires an explicit repeat-import confirmation. Attach images through the composer after import.
- **System:** manual/auth/deployment pause states, token lifetime, heartbeat, backup history, active incidents, email configuration and delivery results.

CSV format:

```csv
text,scheduled_at,alt_text
"An idea for later",,
"A scheduled conversation",2026-12-01 09:00:00,
```

`text` is required; blank `scheduled_at` means draft. Schedules must be future dates in the **server's local timezone**, displayed throughout the dashboard. There is no automatic browser-to-server timezone conversion. Keep the machine timezone stable after scheduling; Nigeria uses `Africa/Lagos`. Duplicate columns and invalid/past schedules are rejected.

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

Install this code update with `sudo bash deploy/install.sh`. For later changes to `.env` only, restart the dashboard and worker; the independent monitor reads updated settings on its next run. The System page shows **Resend configured** once all fields are present; this checks presence, not whether the credentials or domain are accepted by Resend.

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
venv/bin/python -m app.backup verify --snapshot backups/snapshot-EXAMPLE
venv/bin/python -m app.backup restore --snapshot backups/snapshot-EXAMPLE --destination /tmp/postdesk-restore
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

A failed upgrade restores the previous release when one exists and leaves publishing paused. Migrations are additive; rollback retains the current database and audit history rather than silently discarding new records. Pre-migration database/media copies live in `deployment-backups/`. On the first installation there is no prior managed release: failure leaves publishing stopped/paused and the snapshot available for manual recovery. Do not delete failed releases or snapshots until recovery is complete.

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

## Tests and development

```bash
venv/bin/python -m pytest -q tests
node --check app/static/app.js
bash -n deploy/install.sh deploy/rollback.sh deploy/backup.sh
# Optional real-browser checks (uses isolated data and a temporary local server):
venv/bin/pip install -r requirements-dev.txt
venv/bin/python tests/browser_smoke.py
```

Browser checks use Chrome at `/usr/bin/google-chrome`; set `CHROME_BIN` to another Chromium executable, or install Playwright's Chromium when no system browser is available. They check composing, preview, scheduling, pause/resume, pagination/search, unsaved changes, calendar, uncertain recovery, import/delete, activity/system, mobile layouts, keyboard navigation and logout. Screenshots are written to `/tmp/postdesk-*.png`.

Unit/integration checks use temporary storage and mocked LinkedIn/SMTP calls, including concurrent idempotency, session revocation, persistent throttling, legacy migration, uncertain publication, token blocking, retry timing, backup restore and deployment rollback. A supervised live post and actual reboot verification remain operator acceptance steps.

For a temporary local run use `bash run_local.sh` (Windows: `run_local.bat`). It starts a real worker, so only use it with content you intend to publish, and never alongside an installed worker. For dashboard-only development, run `venv/bin/python -m app.dashboard` against isolated runtime paths (`APP_DATA_DIR`, `APP_MEDIA_DIR`, `APP_LOG_DIR`, `APP_BACKUP_DIR`, `DB_PATH`, `TOKEN_PATH`) to avoid altering a live queue.

See [verification results and review screenshots](docs/VERIFICATION.md) for the implementation acceptance record.

### API integration notes

All `/api/*` endpoints except session initialization and login require an authenticated session. Mutations require `X-CSRF-Token`; post creation and imports additionally require a stable `Idempotency-Key` of 8–128 ASCII characters. Replays return the stored result; conflicting key reuse returns 409. All times are server-local wall-clock strings.

- `GET /api/overview`: counts, upcoming posts, recent activity, worker health and independent publishing controls; it no longer returns the entire post history.
- `GET /api/posts?page=1&limit=20&status=&search=`: paginated items/total/page/limit; limit 1–100. Status supports individual post states or `attention`.
- `GET /api/posts/{id}` and `POST /api/posts`, `PUT /api/posts/{id}`, `DELETE /api/posts/{id}`: inspect/create/edit/delete with guarded state transitions; writes use multipart form data.
- `GET /api/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD`: exclusive end, at most 62 days, items plus a truncation flag.
- `GET /api/activity?page=1&limit=20` and `GET /api/system`: audit pagination and sanitized operational status.
- `POST /api/publishing`: JSON `{"paused":true}` sets manual pause only.
- `POST /api/connection/validate`: validates the configured token and clears only authorization blocking on success.
- `POST /api/posts/{id}/resolve`: JSON `{"checked_linkedin":true,"outcome":"draft"}` or `"outcome":"posted"`, with optional `post_urn`; applies only to uncertain posts.
- `POST /api/import`: multipart `file` and optional `confirm_duplicate=true`. Duplicate-file 409 responses include the prior result and `duplicate_import=true`; confirmation must use a new key and keep that confirmed payload unchanged for retries.

Post states are `draft`, `scheduled`, `posting`, `posted`, `failed`, `missed`, and `uncertain`. `next_attempt_at` controls retry timing independently of `scheduled_at`. Control state exposes manual `paused`, `auth_blocked`/`auth_reason`, `deploy_paused`, and computed `can_publish`. Sessions and credentials are never included in operational API responses. Deploy the bundled frontend and backend together because the overview response changed.

### LinkedIn HTTP 426: API version upgrade required

The old `202510` API version was retired on October 15, 2026. The application now pins `202610`, listed in [LinkedIn's version documentation](https://learn.microsoft.com/en-us/linkedin/marketing/versioning?view=li-lms-2026-10). Keep this setting maintained as versions expire; do not automatically invent a version from the current date.

If posting fails with HTTP 426, set `LINKEDIN_VERSION` in the project `.env` to a supported version, then restart the worker and dashboard:

```bash
sudo systemctl restart autoposter-worker autoposter-dashboard
```

The shared `.env` is read at process startup, including by managed releases. A configuration-only fix does not require reinstalling the application. Review the failed post in the queue and explicitly reschedule it after restarting; the version change does not retry failed posts automatically. This error does not by itself require reauthorizing LinkedIn. Install the current source update separately to get the improved explanatory 426 error message on future failures.
