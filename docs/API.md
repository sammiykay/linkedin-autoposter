# API reference

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


[Back to README](../README.md)

## Assisted outreach

All endpoints below require the existing authenticated session. Mutations require CSRF and a unique `Idempotency-Key`; unchanged retries return the stored result, and changed payloads with the same key return 409. Stale versions/revisions also return 409. No endpoint sends a LinkedIn message.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/leads` | Paginated contacts: `page`, `search`, `status`, `campaign`, `band` |
| `POST /api/leads` | Create contact; see [field guide](LEADS.md) |
| `PUT /api/leads/<id>` | Replace editable contact fields; include last `updated` |
| `GET /api/leads/<id>/history` | Latest 100 activity entries |
| `POST /api/leads/<id>/action` | `action`: `replied` or `stop`; optional `note`, reply `disposition` |
| `GET /api/leads/export` | Formula-escaped contact CSV |
| `GET /api/lead-imports/template` | Contact template: `format=csv` or `xlsx`, `layout=simple` or `full` |
| `POST /api/lead-imports/preview` | Multipart `file`; returns token, counts, errors, sample, previous result; CSRF required, no idempotency key needed |
| `POST /api/lead-imports/commit` | `token`, `duplicates`: `skip`/`fill_empty`, optional `confirm_duplicate` |
| `GET, POST /api/campaigns` | List or create campaign |
| `PUT /api/campaigns/<id>` | Update configuration with `revision`; enrolled sequence steps are locked |
| `POST /api/campaigns/<id>/pause` | Boolean `paused` |
| `POST /api/campaigns/<id>/enroll` | Atomic `lead_ids` list, up to 500 |
| `POST /api/campaigns/enrollments/<id>/action` | `pause`, `resume`, `complete`, `stop` |
| `GET /api/lead-tasks` | `scope`: `due`/`upcoming`/`all`, optional campaign, page |
| `POST /api/lead-tasks/<id>/action` | `version`, `action`: `draft` with `text`, `snooze` with `days`, or `sent` with `confirmed:true` |

Campaign configuration: `name`, `description`, IANA `timezone`, `time` (HH:MM), `weekdays` (Monday=0 through Sunday=6), `email_digest`, `criteria` lists (`job_title`, `industry`, `company_size`, `country`), and `steps` objects (`template`, `delay_days`). New lead timestamps are UTC epoch seconds; campaign display and slots use its timezone. Existing deadlines are preserved on schedule edits.

### Bulk campaign imports

| Endpoint | Purpose |
| --- | --- |
| `GET /api/campaign-imports/template` | Download sample steps; `format=csv` (default) or `xlsx` |
| `POST /api/campaign-imports/preview` | CSRF-protected multipart `file`; returns preview token, counts, row errors, duplicate names, campaign summaries, and any previous result |
| `POST /api/campaign-imports/commit` | JSON `token`, optional `confirm_duplicate`; requires CSRF and `Idempotency-Key` |

Campaign imports are atomic and always create paused campaigns. Duplicate names are skipped, never updated. A preview error returns `token:null` and blocks confirmation. Previews expire after one hour; repeated file imports within 24 hours require confirmation. Contact/campaign preview tokens are kept separate. See [spreadsheet columns and defaults](LEADS.md#bulk-campaign-uploads).
