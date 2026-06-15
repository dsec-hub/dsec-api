# Changelog

All notable changes to the DSEC Agent API are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Post-event reviews (Tally)** — each event can spin up a short, plain-language
  feedback form in **Tally** (rating + what went well + what to improve). New
  self-contained `features/reviews/` (Tally HTTP client, declarative question
  template, service, schemas) mounted on the events resource:
  `POST /events-api/{id}/review-form` (scope `write`, idempotent, `?force=` to
  recreate), `GET /events-api/{id}/review-form` (status + live response count),
  `GET /events-api/{id}/review-form/responses` (submissions mapped to the
  template, with average rating). `events` gains `review_form_id`,
  `review_form_url`, `review_form_created_at`. Two MCP tools added
  (`create_event_review_form`, `get_event_review_responses`). New env:
  `TALLY_API_KEY`, `TALLY_API_BASE` (server-side only; blank disables → 503).
- **Inbound email capture** — `POST /ingest/email` (scope: `ingest`) records every
  inbound email to the `EventLog` with **no LLM spend and no triage** — a dumb,
  idempotent capture (dedup on Gmail `message_id`; a re-send returns
  `status="duplicate"` with `200`). The spam-gate/classify/draft pipeline at
  `/email/process` is layered on later. New Apps Script in
  `integrations/email-capture-forwarder/` fires it on a 15-minute trigger from the
  Gmail mailbox (shares the existing `ingest`-scoped key).
- **Image media** — `media_asset` table + `/media` feature (scope-gated:
  `read` to list, `write` to upload/patch/delete) for events & projects.
  Already-cropped uploads are processed with Pillow into a compressed **WebP**
  (display) and a **PNG** (download), stored in **Supabase Storage** (public
  bucket; service-role key server-side only), with just URLs + metadata in Neon.
  The public `/website` feed now serves each project/event's `image` (primary
  webp), `download` (primary png), and full `media[]` list so dsec-website can
  render real imagery. New env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
  `SUPABASE_STORAGE_BUCKET`, `MEDIA_MAX_UPLOAD_BYTES`, `MEDIA_MAX_DIMENSION`.
- **MCP server** — the whole workspace is exposed over MCP (Model Context
  Protocol) at `/mcp`, so the exec can manage the club from Claude/ChatGPT
  without the dashboard. 30 scope-gated tools (members, finance, events,
  projects, tasks, meetings, documents, sponsors, people) authenticated by API
  key via a pure-ASGI middleware that stamps the key's scopes into a contextvar.
  A setup guide + machine-readable info live at `/mcp-setup`. Adds the `write`
  scope (workspace writes, no LLM spend); `trigger` stays for LLM actions.
- **Workspace features** — REST CRUD (service + router + schemas, same pattern as
  the club-domain tables, all scope-gated) for **projects** (community showcase,
  auto-slug, public/featured flags), **tasks** (Trello-style boards + cards with
  move/reorder and auto-`completed_at`), **meetings**, **documents** (Notion-style
  markdown docs with nesting + per-person deliverables), **sponsors** (CRM
  pipeline), **events**, and **people**. New tables: `project`, `task_board`,
  `task`, `meeting`, `document`; `events` gains `budget_aud`/`grant_aud` and
  `sponsors` gains CRM fields.
- **Meeting-notes AI** — `POST /meetings/{id}/generate-notes` (scope `trigger`)
  turns a transcript into summary + markdown minutes + action items via the
  shared LLM wrapper (same cost cap as the email agent) and files a MeetingNotes
  document.
- **Finance budgets** — `POST /finance/events/{id}/budget` sets an event budget
  and auto-applies a 50% grant; `GET /finance/summary` gives the weekly headline
  (opening/income/expense/closing + total event budgets/grants).
- **Public website feed** — no-auth, per-IP-rate-limited `/website/{projects,events,stats}`
  serving only published data, so the marketing site can show live projects,
  events, and real social-proof stats (live member count, balance) instead of
  hardcoded placeholders.
- **DUSA weekly imports** — `POST /ingest/dusa` (scope `ingest`) receives the two
  weekly DUSA spreadsheets (membership report + Profit & Loss), parses them
  server-side with `openpyxl`, and lands them in Neon. Idempotent on the Gmail
  `message_id` (re-send → `409`). New tables: `dusa_import` (dedup + audit),
  `members` (roster, upserted by student id; `is_current` tracks the latest
  report), `member_report` (weekly stats), `finance_report` + `finance_transaction`
  (P&L snapshots, DUSA sign convention preserved). Adds the `ingest` API-key scope
  and `scripts/create_api_key.py`. A Google Apps Script
  (`integrations/dusa-gmail-forwarder/`) forwards the attachments from the
  committee mailbox; it never parses Excel itself. Verified against the real
  workbooks (92 members, 65 DUSA; P&L opening $1970.09 → closing $1314.09).
- **Tests** — `pytest` + FastAPI `TestClient` suite covering agent-secret auth
  (reject/accept), the email pipeline branches (spam-gate, fyi-no-reply,
  simple-reply, needs-meeting + Cal.com link, classify/draft error degradation,
  never-auto-sends), the LLM cost cap (global + per-key, asserting no spend), the
  domain models/relations, and that migrations apply cleanly. Runs entirely on
  the SQLite fallback with the OpenAI layer mocked — no external services.
- **Migrations** — Alembic, with a baseline migration for the operational tables
  and a second migration adding the club-domain schema. `scripts/migrate.py`
  applies `upgrade head`, `scripts/check_neon.py` reports the live schema state,
  and `scripts/seed.py` loads realistic sample data.
- **Club-domain schema** — `people`, `events`, `sponsors`, `finance` tables (with
  FK relations) as the single source of truth for the exec dashboard (`dsec-app`),
  which reads/writes Neon directly. `dsec-api` owns the schema; it is not exposed
  over HTTP here.

### Changed
- Schema creation now runs through `alembic upgrade head` instead of
  `Base.metadata.create_all`, gated by the new `RUN_MIGRATIONS_ON_STARTUP` setting
  (default true; set false on serverless and migrate as a deploy step).

### Removed
- **Notion integration** — the architecture no longer involves Notion. Removed the
  Notion→Neon sync, the Notion webhook router, the `/admin/sync/notion*` and
  `/public/events` routes, the `NOTION_*` settings, and the `vercel.json` cron.
  Neon is the single source of truth and the dashboard edits it directly; the old
  Notion-mirror `Event` model was replaced by the domain schema above.

### Planned (v2)
- Implement Discord webhook (relay processed-email summaries / alerts to a channel).
- Implement Cal.com webhook (log bookings made via the meeting link; optional Discord notify).
- `POST /public/notify` relay route.
- Optional Redis-backed `RateLimiter` swap-in for going public.

## [1.0.0] — 2026-06-11

Initial scaffold: an extensible FastAPI base with the email agent as v1.

### Added
- **App core** — `create_app()` factory, `/health`, centralised JSON exception
  handling, and OpenAPI docs gated behind basic auth.
- **Config** (`config.py`) — pydantic Settings for all env vars; `.env.example`.
- **Database** (`db.py`, `models.py`) — SQLAlchemy targeting Neon Postgres
  (SQLite fallback for local dev); small pool + `pool_pre_ping` for serverless.
  Models: `EventLog`, `APIKey`, `RateLimit`, `Event`.
- **Auth** (`auth.py`) — `require_agent_secret`, `require_basic_auth`, and the
  `verify_webhook_signature(mode)` dependency factory (discord/calcom/notion).
- **Core** — generic OpenAI wrapper (`core/llm.py`) with token/cost tracking and
  typed `LLMError`; `EventLog` writer (`core/logging.py`); Neon-backed rate
  limiter behind a `RateLimiter` protocol (`core/ratelimit.py`); API-key
  generation/argon2-hashing/verification with scopes (`core/apikeys.py`).
- **Email feature** — `POST /email/process` (agent-secret auth) running the strict
  spam-gate → classify → draft → log pipeline. Spam gate is LLM-free; failures
  degrade to `{"action":"ignore"}`; never auto-sends.
- **Public API** — API-key-authenticated, scoped, rate-limited routes:
  `/public/status`, `/public/logs`, `/public/events` (read) and `/public/draft`
  (trigger, cost-capped before any LLM call).
- **Admin API** — basic-auth key management (`/admin/keys` create/list/revoke,
  raw key shown once) and `/admin/sync/notion`.
- **Events sync** — single `sync_notion_events()` (Notion→Neon upsert +
  soft-delete) invoked by webhook, Vercel Cron (`/admin/sync/notion/cron`), and
  manual admin endpoint. Notion fetch stubbed pending v2.
- **v2 stubs** — `discord` and `calcom` webhook routers (501), and a `notion`
  webhook handling the verification handshake and driving the events sync.
- **Dashboard** — `GET /dashboard/`, basic-auth, server-rendered audit log over
  `EventLog` with source/action filters.
- **Deploy** — `vercel.json` cron entry, `requirements.txt`, `.gitignore`, and
  docs (`docs/architecture.md`, `api.md`, `configuration.md`, `deployment.md`,
  `extending.md`).
