# Deployment — Vercel + Neon

Both dsec.club (Next.js) and this server deploy to Vercel as **two separate
projects**. This is the second project. FastAPI is officially supported on
Vercel's Python runtime and auto-detected from `requirements.txt` — no adapter or
`vercel.json` gymnastics needed (the only `vercel.json` here is for the cron).

## How Vercel runs it

The entire FastAPI app becomes a **single Vercel Function** (Fluid Compute,
scales with traffic). The entrypoint is `app/main.py` exposing a FastAPI instance
named `app`. Keep the bundle under the 500 MB function limit.

## Serverless constraints (designed around)

- **No persistent in-process state.** Functions are stateless and ephemeral —
  anything in memory or `/tmp` is lost on cooldown. No background threads, no
  in-memory caches relied on across requests. This is *why* the rate limiter is
  Neon-backed, not an in-process counter.
- **All durable state lives in Neon.**
- **Connections.** A function can open a fresh DB connection per invocation,
  exhausting Neon's limit fast. Use Neon's **pooled (pgBouncer)** connection
  string, keep SQLAlchemy `pool_size` small (configured to 5), and rely on
  `pool_pre_ping=True` to survive Neon's idle-compute suspension.
- **Cold starts** add a little latency to the first hit after idle — fine for
  webhooks/cron/internal API; a cold email draft may take an extra second or two.

## Setup steps

1. **Create a new Vercel project** from this repo (root = this directory). The
   Python runtime is auto-detected; no build command required.
2. **Provision Neon** and copy the **pooled** connection string. Convert it to the
   SQLAlchemy + psycopg form:
   ```
   postgresql+psycopg://USER:PASS@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require
   ```
   (Note the `-pooler` host segment and `sslmode=require`.)
3. **Set environment variables** (Settings → Environment Variables) — every var
   from `.env.example`. Minimum for production:
   `AGENT_SECRET`, `OPENAI_API_KEY`, `DATABASE_URL`, `DASHBOARD_USER`,
   `DASHBOARD_PASS`, `CRON_SECRET`.
4. **Cron.** `vercel.json` registers:
   ```json
   { "crons": [{ "path": "/admin/sync/notion/cron", "schedule": "0 6 * * *" }] }
   ```
   Vercel sends `Authorization: Bearer <CRON_SECRET>`; the route validates it.
   This is the daily reconciliation safety net for the Notion→Neon sync.
5. **Deploy** — push to the connected branch or `vercel --prod`.
6. **(Recommended)** Put **Cloudflare** (free tier) in front for WAF/DDoS/bot
   mitigation. App-level rate limiting handles the rest. Documented, not built.

## Tables

`init_db()` runs on startup and creates tables if missing (idempotent). For a
managed migration workflow later, swap to Alembic — not needed for v1.

## Local vs production DB

- Local dev defaults to SQLite (`sqlite:///./local.db`) — zero external services.
- Models are kept DB-agnostic, but **Neon Postgres is the default and supported
  target**. Set `DATABASE_URL` to the Neon pooled string for anything real.
