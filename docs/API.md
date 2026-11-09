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
