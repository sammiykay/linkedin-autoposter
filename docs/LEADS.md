# Leads and assisted LinkedIn outreach

Postdesk prepares messages and follow-up worklists. **It does not send LinkedIn messages, read your inbox, detect replies, scrape profiles, or automatically enrich contacts.** No messaging API approval is needed for this workflow. Email digests go to the workspace owner, never to leads.

## Start a campaign

1. Open **Leads → Import contacts**. Download a CSV or Excel template, replace the example, upload it, inspect the preview, then confirm.
2. Open **Campaigns → Create campaign**. Choose an IANA timezone, confirm it, select workdays and a reminder time, and edit the message sequence.
3. Optionally enter your target buyer criteria. Separate values with semicolons. Enable owner email digests if Resend is configured.
4. In **Contacts**, select contacts and choose **Enroll selected**. Importing alone never starts a campaign. A contact can have one active or paused enrollment.
5. Use **Due today** for your worklist. Switch to Upcoming or All outstanding to inspect future or paused tasks.

## Sending and following up

Open **Review message**, resolve any missing placeholders, and save edits. Copy the message and open LinkedIn. Send it on LinkedIn yourself, return to Postdesk, check **I sent this message on LinkedIn**, then select **Mark as sent**. Opening or copying never counts as sending.

Each confirmed send schedules the next step relative to that send time, rounded forward to a campaign workday and reminder time. The default sequence has an initial message, a follow-up after three days, and a final follow-up seven days later. You can change these delays before enrollment. Active sequence definitions are locked; create a new campaign or end existing enrollments to change them. Editing workdays/time affects newly scheduled steps, while existing deadlines stay fixed. Snooze moves a task forward to a valid workday.

A due task remains outstanding until you handle it. Downtime does not create repeated message tasks. Campaign pause and individual enrollment pause suppress sending confirmations and daily reminders. Resume preserves outstanding tasks. Publishing pause and expired posting credentials do not pause lead reminders.

When a lead replies, use **Contacts → Record reply** and record interested, neutral, not interested, or unclassified, with optional reply text. Every recorded reply cancels pending follow-ups. **Do not contact** stops outreach and prevents future enrollment. **Complete enrollment** cancels remaining tasks while retaining history.

Status is owner-recorded, not verified against LinkedIn. Avoid marking a message sent unless you actually sent it.

## Spreadsheet format

CSV must include a header row. Excel uses the first worksheet. Both accept 1–500 data rows, a 20 MB request limit, and no Excel formulas. The XLSX expanded content limit is 50 MB. Store phone numbers and external identifiers as text so Excel itself does not drop leading zeros.

| Column | Required | Format / example |
| --- | --- | --- |
| `full_name` | Yes | `Ada Okafor`, up to 200 characters |
| `linkedin_url` | Yes | `https://www.linkedin.com/in/ada-example` |
| `external_id` | No | Your CRM identifier, e.g. `000127` |
| `first_name` | No | `Ada`; required if your draft uses its placeholder |
| `last_name` | No | `Okafor` |
| `email` | No | Contact information only; never used for outreach |
| `phone` | No | Text, e.g. `+234…` |
| `company` | No | `Northstar Studio` |
| `job_title` | No | `Founder` |
| `industry` | No | `Software` |
| `company_size` | No | `1-10`, `11-50`, `51-200`, `201-500`, `501-1000`, `1001+` |
| `country` | No | `Nigeria` |
| `city` | No | `Lagos` |
| `website` | No | Company website, stored as text |
| `tags` | No | `founder;software` |
| `source` | No | `Conference` |
| `notes` | No | Research/context, up to 5,000 characters |
| `message` | No | Initial message for this lead, up to 5,000 characters; supports the documented placeholders |

An imported `message` becomes the initial task draft on enrollment. If blank, the campaign’s first template is used. Follow-ups use the campaign sequence. Editing the contact’s message later does not overwrite an existing task draft; edit that task separately.

Other contact fields allow up to 500 characters. Unknown columns are ignored. Profiles are normalized to `https://www.linkedin.com/in/<slug>` without query strings or trailing slashes. Existing profiles are skipped by default. **Fill empty fields only** preserves populated data, status, history, and campaign progress. Any row error blocks the whole import. Previews expire after one hour; duplicate-file warnings cover 24 hours. Confirming an unchanged import twice returns its original result.

Export contains contact fields, not campaign/task history. Formula-like CSV values are prefixed with an apostrophe to reduce spreadsheet formula execution risk; this may be visible when opened as plain text. Full database backups preserve campaigns and history.

## Personalization and scores

Templates support `{{full_name}}`, `{{first_name}}`, `{{last_name}}`, `{{company}}`, `{{job_title}}`, and `{{campaign_name}}`. Missing fields stay visibly unresolved and prevent send confirmation. Edit the contact or save an explicitly personalized draft. Drafts and templates support plain text up to 5,000 characters, with no attachments in this version.

Fit scores are **explainable rules**, not machine learning or a prediction of purchase. Configure criteria per campaign. Role keywords use case-insensitive substring matching; industry, company-size band, and country use exact case-insensitive matching. Weights are role 40, industry 30, size 20, location 10, normalized over configured criteria. Missing information scores no points. Expand a score to see each reason. No criteria means Not configured. Bands: low below 40, medium 40–69, high 70+. Reply disposition remains separate from fit. Editing contact or campaign criteria changes the next displayed calculation.

## Background reminders and delivery

The existing worker runs the lead reminder cycle even when publishing is paused or its token is missing. The independent monitor timer also runs this cycle and flushes the persistent email outbox. An enabled campaign gets at most one daily digest on a selected workday, after its reminder time, when it has due active tasks. Multiple cycles/services cannot enqueue duplicate daily digests. Missing email configuration leaves dashboard tasks available and displays **Email alerts not configured**. A failed email remains in the existing retry outbox; delivery errors appear in **System & settings**. Digests contain counts and campaign IDs, not lead details or message text.

The computer must be awake and online to deliver email. Reboot resumes installed services and timers. Overdue tasks remain visible after downtime. No historical digest backlog is generated.

## Activation, backups, and recovery

From the repository root, run `sudo bash deploy/install.sh` to prepare/test a release and activate it using the existing deployment checks. No campaigns are created or activated by migration. Existing posts, tokens, pause controls, and services remain intact. A deployment backup is created before migration; retain it and the previous release for rollback as described in [Operations](OPERATIONS.md).

Normal content snapshots now include all lead tables in SQLite. Treat snapshots as private because they contain contact information and message history. Restore only into an isolated directory first. After restoration, review due work and previous manually recorded sends before continuing. The initial implementation's contacts/history are retained by migration.

Validation commands:

```bash
venv/bin/python -m pytest tests -q
venv/bin/python tests/leads_browser.py
```

Tests use temporary storage. Browser tests do not start a publishing worker or contact LinkedIn. No live messaging is needed for validation.
