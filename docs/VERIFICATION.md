# Reliability upgrade verification

Implemented in the local codebase; production activation requires the system installer.

| Area | Delivered |
| --- | --- |
| Publishing safety | Transactional request keys and import fingerprints; attempt phases; uncertain state and acknowledged recovery; bounded retries with Retry-After |
| Authorization | Separate persistent manual, authorization and deployment blocks; scheduled queue preservation; atomic private token writes; validated reconnect |
| Storage/API | Additive migrations v1/v2; guarded mutations with audit events; bounded pagination and calendar queries |
| Sessions | Hashed server-side session IDs, logout revocation, password-change invalidation and persistent throttling |
| Backups | Daily timer, consistent database/media snapshots, checksums, completion manifest, private permissions, 14-day successful-snapshot retention and isolated restore |
| Monitoring | Independent timer, persisted incidents, delivery outbox with leases/retries, recovery messages, backup/worker/token/overdue/disk checks and visible unconfigured SMTP |
| Deployment | Separate tested source/environment releases, pre-migration snapshots, drain/stop/migrate/readiness sequence, previous-release rollback and fail-closed publishing |
| Dashboard | Light/blue responsive layout; queue table/cards; calendar/agenda; composer/image preview; audit history; recovery and system views; keyboard navigation and unsaved-edit protection |

## Automated evidence

- `venv/bin/python -m pytest -q tests`: **46 passed**. Includes actual isolated worker-process restarts, concurrent request replays, session invalidation, queue preservation, uncertain outcomes, backup restore/retention, alert failures and deployment success/failure/rollback.
- `venv/bin/python tests/browser_smoke.py`: **passed**, with no JavaScript errors. Includes desktop/mobile layouts, attached image preview, composing/scheduling, pause/resume, pagination/search, unsaved-edit protection, calendar, uncertain recovery, imports/deletion, activity/system pages and sign-out.
- Browser retry scenario: intentionally drops the success response for a confirmed duplicate import; retrying the request returns the original result without inserting extra posts.
- `node --check app/static/app.js`, Python compilation and deployment shell syntax checks: passed.
- `systemd-analyze verify` on rendered worker/dashboard/backup/monitor units and both timers: exit 0. The host emitted unrelated warnings for pre-existing third-party services.

All automated work uses isolated storage. Browser checks never start the publishing worker. No real LinkedIn posts or SMTP messages were sent by tests.

## Operator acceptance

The implementation session could not activate privileged system changes because `sudo -n true` returned `sudo: a password is required`. Run `sudo bash deploy/install.sh` from the project directory, then follow README.md for health, timer, authorization, SMTP and supervised reboot checks. Existing services were not restarted by this session. A real reboot, first intended live post and actual email delivery remain operator checks, as specified in the plan.

First managed installation has no previous managed release. Its pre-migration snapshot enables manual recovery; automatic source rollback becomes available once there is a prior installed release. Local content backups do not protect against loss of the computer, and do not contain credentials.

## Screenshots

These screenshots contain generated test posts, not live queue data.

- [Dashboard](dashboard-preview.png)
- [Composer](composer-preview.png)
- [Calendar](calendar-preview.png)

## Resend email integration

Resend HTTPS delivery is implemented with stable per-alert idempotency keys, sanitized errors, Retry-After handling and provider acceptance receipts. SMTP is retained as an explicit legacy option. Schema migration v2 adds provider and receipt fields to the outbox. The System page exposes provider/configuration status and receipts, never the API key.

The additional mocked tests cover request payload/authentication, successful receipts, retry-key reuse, rate limiting, authorization/server/transport failures, malformed receipts, incomplete settings, API secret redaction and sending only an explicitly requested test alert. No real email was sent. `python -m app.alerts status` currently reports that `RESEND_API_KEY`, `RESEND_FROM` and `ALERT_TO` need configuration in the private `.env` file.
