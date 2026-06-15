# DUSA weekly-report forwarder (Google Apps Script)

Pulls the two weekly DUSA emails out of a Gmail mailbox and forwards their
`.xlsx` attachments to the DSEC API, which parses and ingests them into Neon.

| Report | From | Subject contains | Server parser |
|---|---|---|---|
| Membership | `memberships@dusa.deakin.edu.au` | "club members weekly report" | `report_type=membership` |
| Profit & Loss | `dusa-accounts@deakin.edu.au` | "Profit and Loss Report" | `report_type=pnl` |

The script is a **thin forwarder**: it never parses Excel. It sends the raw
workbook (multipart `file`) plus metadata to `POST /ingest/dusa`, and the API
parses it with `openpyxl` server-side (testable, versioned, handles the messy
multi-sheet P&L workbook). See `app/features/ingest/` in `dsec-api`.

## Why a forwarder and not server-side IMAP

The reports arrive in a committee Gmail inbox. Apps Script binds to that
mailbox with the user's own OAuth — no IMAP password to store, no inbox
credentials in the API. The API only ever sees an authenticated upload.

## Install

1. Create a new Apps Script project at <https://script.google.com> **signed in
   as the mailbox that receives the DUSA emails** (or a delegate with access).
2. Paste `Code.gs`. Set the manifest (`appsscript.json`) via
   Project Settings → "Show appsscript.json".
3. Project Settings → **Script properties** → add:
   - `DSEC_API_KEY` = a key with the `ingest` scope (mint one in `dsec-api`:
     `python -m scripts.create_api_key --scopes ingest --label "gmail-forwarder"`).
   - `DSEC_API_BASE` = `https://api.dsec.club` (optional; this is the default).
4. Run `setup()` once and approve the OAuth consent screen. This creates the
   `DSEC/Ingested` label and a **daily 9am** trigger (`ingestWeeklyReports`).
5. (Optional) Run `ingestWeeklyReports()` by hand to backfill the last 21 days.

## Idempotency

Triple-guarded so re-runs are always safe:

- each processed Gmail **message id** is recorded in Script Properties;
- the thread is **labelled** `DSEC/Ingested` and excluded from the search;
- the API **dedupes on `message_id`** and returns `409` for a repeat (which the
  script treats as success).

## What gets sent

`POST {DSEC_API_BASE}/ingest/dusa` — `multipart/form-data`, `Authorization: Bearer <key>`:

| field | example |
|---|---|
| `file` | the `.xlsx` blob |
| `report_type` | `membership` \| `pnl` |
| `message_id` | Gmail message id (dedup key) |
| `received_at` | ISO 8601 of the email date |
| `sender` | `DUSA <memberships@dusa.deakin.edu.au>` |
| `subject` | the email subject |
| `filename` | `Club Weekly Membership Report Schedule.xlsx` |

## Tuning

- Reports land **Friday**; the daily trigger means a missed run self-heals.
- `SEARCH_WINDOW` (`newer_than:21d`) only bounds how much Gmail is scanned;
  dedup makes a wider window harmless.
- Subjects/senders are in `REPORT_TYPES` at the top of `Code.gs` — update there
  if DUSA changes their templates.
