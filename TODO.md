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
      `OPENAI_API_KEY`, Neon **pooled** `DATABASE_URL`, dashboard creds, `CRON_SECRET`).
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
- [ ] Consider a per-thread dedupe so re-delivered messages don't re-draft.

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

- [ ] **Decision-maker stage** after classify: an LLM call that, given the email +
      a compact snapshot of open events, returns a *structured action* (JSON /
      tool-call), not just a draft. Start with one action type: `update_dusa_status`.
- [ ] **Event matching**: resolve the email to an `Event` (by name fuzzy-match /
      explicit DUSA reference / thread). Must be conservative — never mutate on a
      low-confidence match; fall back to "draft + flag for human".
- [ ] **Action executor**: apply `update_dusa_status(event_id, status)` against the
      DB. `status` must be one of the dashboard's `DUSA_STATUSES`
      (`Not Started | Submitted | Approved | Rejected | Not Required`); also flip
      `dusa_approved` when moving to/away from `Approved`.
- [ ] **Auditability**: log every decision to `EventLog` (action taken, target
      event, confidence, the email that triggered it) so the dashboard audit view
      shows *why* a card moved. No silent writes.
- [ ] **Guardrails**: dry-run / human-confirm mode for the first rollout; cap the
      blast radius (one event per email, no destructive actions); reuse the
      `GLOBAL_DAILY_LLM_CAP` budget.
- [ ] **Dashboard reflection**: the dusa kanban reads `dusa_submission_status`
      directly, so a correct DB write is all the front-end needs — verify the
      `/events/dusa` board reflects agent-driven moves.
- [ ] (Later) Generalise beyond DUSA: same decision-maker pattern could update
      sponsor stages, finance, meeting notes, etc. Keep the action registry small
      and explicit per type.

## v2 integrations
- [ ] **Discord webhook**: Ed25519 verification + relay/alert logic.
- [ ] **Cal.com webhook**: HMAC verification + log booking → optional Discord notify.
- [ ] **`POST /public/notify`**: relay to Discord (trigger-scoped).

## Service continuity (future)
Portal is intended to stay free as it passes between committees. To support that:
- [ ] **Service migration wizard**: export the full workspace (DB snapshot, env var
      manifest, API key list) into a portable bundle so the next committee can
      re-deploy to a fresh Vercel + Neon project without manual archaeology.
- [ ] **Static archive + storage cleanup**: download all uploaded/stored assets as a
      zip so they can be preserved off-platform, then wipe them from the live storage
      bucket — keeps Neon/Vercel/blob storage within the free tier for the next team.
- [ ] **Archive dashboard view**: mark an event or finance record as "archived" (read-
      only, hidden from active lists) rather than deleting it, so the next committee
      has a clean slate without losing history.
- [ ] Triggered from the Admin API (`/admin/archive/export`) so it can be run once
      at end-of-year handover; should require the `admin` scope and log to `EventLog`.

## Platform / ops
- [x] Adopt Alembic for migrations (replaced `create_all`; baseline migration +
      `scripts/migrate.py` + `scripts/check_neon.py`; gated by `RUN_MIGRATIONS_ON_STARTUP`).
- [ ] Optional Redis `RateLimiter` impl for when the API goes public.
- [x] Basic test suite (pytest + TestClient) covering auth, caps, pipeline branches.
- [ ] Request-size enforcement middleware using `MAX_REQUEST_BYTES`.
- [ ] Structured/JSON logging for Vercel log drains.
