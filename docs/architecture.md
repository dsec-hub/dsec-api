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
  db.py                # SQLAlchemy engine/session, init_db(), get_db dependency
  models.py            # ORM models: EventLog, APIKey, RateLimit, Event
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
    events/            # sync_notion_events() + manual/cron sync routes
    discord/           # v2 stub (501)
    calcom/            # v2 stub (501)
    notion/            # webhook: verification handshake + drives event sync
  dashboard/           # GET /dashboard — server-rendered audit log
```

## Core modules (shared by every feature)

| Module | Responsibility |
|---|---|
| `db` | Engine + session. Neon (Postgres) in prod, SQLite fallback for local dev. Small pool + `pool_pre_ping` for serverless. |
| `auth` | Three reusable deps: shared-secret header, basic auth, and a webhook-signature **factory** (`discord`/`calcom`/`notion` modes). |
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

## Data flow — events (Notion → Neon → site)

```
Notion (committee edits)
   │   webhook / cron / manual
   ▼
sync_notion_events()  ── upsert + soft-delete ─►  Neon `Event` table
                                                      │
                                                      ▼
                                          dsec.club reads Neon DIRECTLY
```

FastAPI owns **ingest/writes**; it is not in the site's **read** path. The
public `GET /public/events` route exists only for non-website internal tools.

## Shared log table

Every integration logs to one `EventLog` (`source` column = email/discord/
calcom/notion). The dashboard shows all activity in one place from day one.

## Serverless model

Runs as a single Vercel Function (Fluid Compute). No persistent in-process
state: no in-memory counters, caches, or background threads relied on across
requests. This is *why* the rate limiter is Neon-backed — an in-process counter
wouldn't survive between invocations. See [`deployment.md`](deployment.md).
