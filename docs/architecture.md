# Architecture

The DSEC Agent API is an **extensible base** that mounts self-contained feature
modules. The email agent is v1; it is deliberately *one plugin among many* so new
inbound integrations bolt on without touching existing code.

## Design principle

> A new integration must require **zero edits to existing feature folders** —
> only a new folder under `app/features/` plus one `include_router` line in
> `app/main.py`. If a change breaks that, the design is wrong.

Everything shared lives in the **core**; every feature reuses it.

## Layout

```
app/
  main.py              # app factory: mounts routers, exception handlers, gated docs
  config.py            # pydantic Settings (env-driven)
  db.py                # SQLAlchemy engine/session, run_migrations(), get_db dependency
  models.py            # ORM models: operational (EventLog/APIKey/RateLimit) + domain
  auth.py              # require_agent_secret, require_basic_auth, verify_webhook_signature
  core/
    llm.py             # generic OpenAI wrapper (classify/generate), LLMError
    logging.py         # log_event() → EventLog, with token/cost tracking
    ratelimit.py       # RateLimiter protocol + NeonRateLimiter
    apikeys.py         # key gen/hash/verify, require_api_key(*scopes)
  features/
    email/             # POST /email/process — the Gmail endpoint (v1)
    public/            # API-key auth, rate-limited external API
    admin/             # basic-auth key management
    discord/           # v2 stub (501)
    calcom/            # v2 stub (501)
  dashboard/           # GET /dashboard — server-rendered audit log

alembic/               # versioned migrations (two: operational + domain schema)
scripts/               # migrate.py · check_neon.py · seed.py (ops/dev helpers)
tests/                 # pytest suite (run: .venv/bin/python -m pytest)
```

## Core modules (shared by every feature)

| Module | Responsibility |
|---|---|
| `db` | Engine + session. Neon (Postgres) in prod, SQLite fallback for local dev. Small pool + `pool_pre_ping` for serverless. |
| `auth` | Three reusable deps: shared-secret header, basic auth, and a webhook-signature **factory** (`discord`/`calcom` modes). |
| `core.llm` | Email-agnostic OpenAI wrapper. Returns text + tokens + estimated cost. Raises typed `LLMError` so callers degrade gracefully. |
| `core.logging` | One `log_event()` writing the shared `EventLog`. Never raises into callers. |
| `core.apikeys` | `dsec_live_<token>` generation, argon2 hashing, `require_api_key(*scopes)` verification dependency. |
| `core.ratelimit` | `RateLimiter` protocol + Neon-backed fixed-window impl. Per-key, per-IP, per-key-daily-trigger, and global-daily-LLM caps. |

## Request flow — email

```
Gmail Apps Script
   │  POST /email/process  (X-Agent-Secret)
   ▼
require_agent_secret ──► run_pipeline()
                           │ 1. spam gate (NO LLM)  ── junk ─► ignore
                           │ 2. classify (cheap model) ── fyi ─► ignore
                           │ 3. draft (draft model)  [needs-meeting → +CALCOM_LINK]
                           │ 4. log_event() → EventLog
                           ▼
                    {action: draft|ignore, draftBody?}
```

Any error in classify/draft is logged and downgraded to `{"action":"ignore"}` —
the endpoint never 500s to the Apps Script (which would retry forever).

## Data model — Neon is the single source of truth

`dsec-api` owns the Neon schema but is **not** a data API for it. The club's
records are edited entirely through `dsec-app` — a separate, NextAuth-gated
Next.js dashboard (not in this repo) that reads and writes Neon directly.

```
Exec ──► dsec-app (Next.js, NextAuth) ──reads/writes──► Neon Postgres
                                                          ▲  single source of truth
                                                          │
                                  dsec-api ──owns schema──┘  (email/automation;
                                                              no domain HTTP API)
```

Two table groups share the database:

* **Operational** — `event_log`, `api_key`, `rate_limit`. Read/written by this
  service for the audit log, public API keys, and the rate limiter.
* **Club domain** — `people`, `events`, `sponsors`, `finance`. The dashboard's
  source of truth. Plain nullable FKs relate them (`events.event_lead_id` →
  `people`, `sponsors.contact_person_id` → `people`, `finance.related_event_id`
  → `events`), and every domain row carries `created_at`, `updated_at`, and an
  `archived` soft-delete flag (the app never hard-deletes).

`dsec-api` deliberately exposes **none** of the club-domain tables over HTTP.

> _History: an earlier scaffold synced events from Notion into Neon (webhook +
> cron + manual triggers). That Notion integration was removed; Neon is now
> authoritative and `dsec-app` performs the edits._

## Schema & migrations

The schema is versioned with **Alembic** (`alembic/`, two migrations: an
operational baseline, then the club-domain tables). On startup `run_migrations()`
applies `alembic upgrade head` when `RUN_MIGRATIONS_ON_STARTUP` is set (default
true); on serverless, disable it and migrate as a deploy step
(`scripts/migrate.py`). Helpers: `scripts/check_neon.py` reports which tables
exist in a live DB, and `scripts/seed.py` loads sample domain data.

## Shared log table

Every integration logs to one `EventLog` (`source` column = email/discord/
calcom). The dashboard shows all activity in one place from day one.

## Serverless model

Runs as a single Vercel Function (Fluid Compute). No persistent in-process
state: no in-memory counters, caches, or background threads relied on across
requests. This is *why* the rate limiter is Neon-backed — an in-process counter
wouldn't survive between invocations. See [`deployment.md`](deployment.md).
