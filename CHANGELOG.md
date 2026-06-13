# Changelog

All notable changes to the DSEC Agent API are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Tests** — `pytest` + FastAPI `TestClient` suite covering agent-secret auth
  (reject/accept), the email pipeline branches (spam-gate, fyi-no-reply,
  simple-reply, needs-meeting + Cal.com link, classify/draft error degradation,
  never-auto-sends), and the LLM cost cap (global + per-key, asserting no spend).
  Runs entirely on the SQLite fallback with the OpenAI layer mocked — no
  external services required.
- **Migrations** — Alembic introduced with a baseline migration for the current
  models (`EventLog`, `APIKey`, `RateLimit`, `Event`). `scripts/migrate.py`
  applies `upgrade head`; `scripts/check_neon.py` reports the live schema state.

### Changed
- Schema creation now runs through `alembic upgrade head` instead of
  `Base.metadata.create_all`. Startup migration is gated by the new
  `RUN_MIGRATIONS_ON_STARTUP` setting (default true; set false on serverless and
  run migrations as a deploy step).

### Planned (v2)
- Implement Discord webhook (relay processed-email summaries / alerts to a channel).
- Implement Cal.com webhook (log bookings made via the meeting link; optional Discord notify).
- Implement the real Notion fetch in `sync_notion_events()` + `X-Notion-Signature` verification.
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
