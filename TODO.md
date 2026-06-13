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
- [ ] Smoke-test `/email/process` from the actual Gmail Apps Script.
- [ ] Mint the committee's first API keys via `/admin/keys`.
- [ ] (Recommended) Put Cloudflare in front of the deployment.
- [ ] Tune `RATE_LIMIT_*` and `GLOBAL_DAILY_LLM_CAP` to real usage/budget.

## Email feature — hardening
- [ ] Expand spam heuristics from real inbox samples (false-positive review).
- [ ] Validate classify labels against a small eval set; tune prompts/models.
- [ ] Consider a per-thread dedupe so re-delivered messages don't re-draft.

## v2 integrations
- [ ] **Notion sync**: implement real `_fetch_notion_events()` (Notion API,
      pagination, property → `NotionEvent` mapping) and `X-Notion-Signature` HMAC.
- [ ] **Discord webhook**: Ed25519 verification + relay/alert logic.
- [ ] **Cal.com webhook**: HMAC verification + log booking → optional Discord notify.
- [ ] **`POST /public/notify`**: relay to Discord (trigger-scoped).

## Platform / ops
- [x] Adopt Alembic for migrations (replaced `create_all`; baseline migration +
      `scripts/migrate.py` + `scripts/check_neon.py`; gated by `RUN_MIGRATIONS_ON_STARTUP`).
- [ ] Optional Redis `RateLimiter` impl for when the API goes public.
- [x] Basic test suite (pytest + TestClient) covering auth, caps, pipeline branches.
- [ ] Request-size enforcement middleware using `MAX_REQUEST_BYTES`.
- [ ] Structured/JSON logging for Vercel log drains.
