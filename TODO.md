# TODO

Tracking work beyond the v1 scaffold. Grouped by area; checked = done.

## v1 — shipped ✅
- [x] App factory, `/health`, exception handling, gated docs
- [x] Config (pydantic Settings) + `.env.example`
- [x] DB (Neon/SQLite), models: EventLog / APIKey / RateLimit / Event
- [x] Auth: agent secret, basic auth, webhook-signature factory
- [x] Core: llm, logging, ratelimit, apikeys
- [x] Email pipeline: spam gate → classify → draft → log
- [x] Public API (read/trigger, scoped, rate-limited)
- [x] Admin API (key mgmt + manual sync)
- [x] Events sync (one fn, three triggers — Notion fetch stubbed)
- [x] Discord / Cal.com / Notion routers (stubs + handshake)
- [x] Dashboard (audit log)
- [x] Docs, CHANGELOG, vercel.json

## Before first production deploy
- [ ] Create the Vercel project; set all env vars (real `AGENT_SECRET`,
      `ANTHROPIC_API_KEY`, Neon **pooled** `DATABASE_URL`, dashboard creds, `CRON_SECRET`).
- [ ] Provision Neon; confirm pooled connection string + `sslmode=require`.
- [ ] **Image storage**: set `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, and
      create a **public** Storage bucket named `SUPABASE_STORAGE_BUCKET` (default
      `media`). Without the bucket, `POST /media` fails — uploads now surface a
      clear 503 ("bucket not found"), not a silent 500. Smoke-test one upload.
- [ ] Smoke-test `/email/process` from the actual Gmail Apps Script.
- [ ] Mint the committee's first API keys via `/admin/keys`.
- [ ] (Recommended) Put Cloudflare in front of the deployment.
- [ ] Tune `RATE_LIMIT_*` and `GLOBAL_DAILY_LLM_CAP` to real usage/budget.

## Email feature — hardening
- [ ] Expand spam heuristics from real inbox samples (false-positive review).
- [ ] Validate classify labels against a small eval set; tune prompts/models.
- [x] Per-message dedupe so re-delivered messages don't re-draft
      (`pipeline._already_processed`; keys on `messageId`, only dedupes a durable
      draft/decision outcome so a cap-hit/LLM-error ignore stays retryable).
- [x] Bring the agent-secret `/email/process` path under `GLOBAL_DAILY_LLM_CAP`
      (`ratelimit.check_and_count_llm_global`; keyless spend now counted + bounded).

## Email decision-maker → DUSA pipeline (planned)
As of 2026-06-15 **nothing here is deployed**: no Gmail Apps Script exists and no
Gmail/Hostinger (dsec.club) email config has been done. The first, log-only step is
the capture forwarder (`POST /ingest/email`, see `integrations/email-capture-forwarder/`).
This planned work then assumes a forwarder feeds `POST /email/process`, which today
only triages + drafts a reply (spam gate → classify → draft → log). The goal: turn the LLM into a **decision-maker** that can also *act on the
workspace* off the back of an email — most importantly, keep the dashboard's
`/events/dusa` pipeline (the kanban on the `Event.dusa_submission_status` column)
in sync without anyone touching it by hand.

Worked example: DUSA emails "your event *X* is approved" → the agent matches it to
the right `Event` and sets `dusa_submission_status = "Approved"` (and
`dusa_approved = true`). An email saying the submission needs more info / action →
move it to a "needs attention" state. A submission confirmation → `"Submitted"`.

# STATUS 2026-08-29: the MVP below is BUILT and ships DARK (branch
# feat/api-continuation). Flags default safe: EMAIL_DECISION_MAKER_ENABLED=False,
# EMAIL_DECISION_DRY_RUN=True. Turn on to propose; drop dry-run to apply. One
# outstanding scope decision noted after the list.
- [x] **Decision-maker stage** after classify: `llm.decide()` returns a structured
      JSON action against a compact snapshot of open events. One action type today:
      `update_dusa_status`. (`email/pipeline.py` decision stage, `email/actions.py`.)
- [x] **Event matching**: `email/matching.py` — conservative stdlib-difflib matcher
      (exact-subject/body beats fuzzy; refuses near-ties; confidence-scored). Below
      `EMAIL_MATCH_MIN_CONFIDENCE` it never mutates → drafts + flags for a human.
- [x] **Action executor**: `email/actions.py` applies `update_dusa_status(event_id,
      status)` via `events.service.set_dusa_status`; `status` validated against
      `events/dusa.py::DUSA_STATUSES`. Target is resolved by the deterministic
      matcher, NOT a trusted LLM id (an LLM id that disagrees is flagged).
      NOTE: TODO said also flip `Event.dusa_approved` — that column does NOT exist
      on `Event` (it's a Sponsor field), and the kanban reads `dusa_submission_status`
      only, so the MVP writes just the status. Add an `Event.dusa_approved` column +
      migration first if that flag is genuinely wanted.
- [x] **Auditability**: every decision (including `none`) logs an `EventLog`
      `email_decision` row — action, target event, confidence, dry-run/applied,
      reason, `messageId`. No silent writes.
- [x] **Guardrails**: dry-run/human-confirm defaults; exactly one event per email;
      no destructive actions; the email path now counts against `GLOBAL_DAILY_LLM_CAP`.
- [x] **Dashboard reflection**: a correct `dusa_submission_status` write is all the
      hub kanban needs (it reads Neon directly). No API board endpoint here to test
      against — verify live once the flag is enabled.
- [ ] (Later) Generalise beyond DUSA: same decision-maker pattern could update
      sponsor stages, finance, meeting notes, etc. Keep the action registry small
      and explicit per type.

## v2 integrations
- [x] **Discord webhook**: Ed25519 verification (SEC-03, fail-closed in prod) + the
      inbound interaction handler. Outbound *alerts INTO* Discord now exist via the
      relay helper below.
- [x] **Cal.com webhook**: HMAC verification (fail-closed in prod) + booking →
      SponsorLead, plus an optional Discord alert (`CALCOM_NOTIFY_DISCORD`, and it
      never posts the booker's raw email).
- [x] **`POST /public/notify`**: trigger-scoped relay to Discord via an incoming
      webhook (`core.notify.notify_discord`, `DISCORD_NOTIFY_WEBHOOK_URL`; 503 when
      unconfigured, every relay logged to EventLog).

## Service continuity (future)
Portal is intended to stay free as it passes between committees. To support that:
- [x] **Service migration wizard (manifest)**: `GET /admin/archive/export` returns a
      portable, secret-free JSON bundle — schema snapshot + per-table row counts +
      alembic revision + env var NAMES (flagged sensitive, never values) + API key
      list (metadata, never hashes) — so the next committee can re-deploy to a fresh
      Vercel + Neon without archaeology. Basic-auth (dashboard owner) + audited.
      NOTE: this is a *manifest*, not a data dump. A genuine row-level snapshot of
      student PII is deliberately out of scope for an HTTP endpoint — use `pg_dump`
      with DB creds for that.
- [ ] **Static archive + storage cleanup**: download all uploaded/stored assets as a
      zip so they can be preserved off-platform, then wipe them from the live storage
      bucket — keeps Neon/Vercel/blob storage within the free tier for the next team.
      (NOT built — the wipe is destructive on live storage; needs an owner decision.)
- [x] **Archive dashboard view (now reversible)**: every domain record already had an
      `archived` soft-delete column + `archive_*` action and is hidden from active
      lists; soft-delete was **one-way** (no undo). Added `unarchive_*` across REST +
      MCP + catalog for all 11 archivable modules (events, people, sponsors, projects,
      tasks/boards, meetings, documents, partners, links, scan) so a mistaken archive
      is recoverable. `FinanceEntry` has an `archived` column but no CRUD surface, so
      there's nothing to (un)archive there yet — noted, not wired.
- [x] Triggered from the Admin API (`/admin/archive/export`), basic-auth (the
      dashboard-owner credential — this codebase has no `admin` API-key scope; basic
      auth is the highest-privilege gate) and logged to `EventLog` (`archive_export`).

## Platform / ops
- [x] Adopt Alembic for migrations (replaced `create_all`; baseline migration +
      `scripts/migrate.py` + `scripts/check_neon.py`; gated by `RUN_MIGRATIONS_ON_STARTUP`).
- [ ] Optional Redis `RateLimiter` impl for when the API goes public.
- [x] Per-module scopes for the PII-heavy modules (`read:members`, `read/write:people`,
      `read/write:documents`) across REST + MCP + the scope catalog. Legacy read/write
      keys still satisfy them (backward-compatible). Remaining blanket modules (events,
      tasks, meetings, projects, partners, links, media, attachments, scan, reviews)
      are still coarse — roll out the same way if/when least-privilege minting needs them.
- [x] `rate_limit` unique key is now bucket-aware with `NULLS NOT DISTINCT`
      (migration `b1d4f7a9c3e2`) — fixes the per-IP duplicate-row split AND a latent
      00:00-UTC collision between the `req` and `trigger` rows. **Owner: run
      `alembic upgrade head` on Neon at the next deploy** (Postgres-only migration).
- [x] Basic test suite (pytest + TestClient) covering auth, caps, pipeline branches.
- [x] Request-size enforcement middleware using `MAX_REQUEST_BYTES` (413, exempts
      the multipart upload routes; see `_register_request_size_limit` in main.py).
- [x] Structured/JSON logging for host log drains (`core/logconfig.py`,
      `LOG_FORMAT=json|text` default text — a no-op until switched on, `LOG_LEVEL`).
