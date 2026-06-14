# Deployment — Vercel + Neon

The club's Next.js front-ends (including the internal `dsec-app` exec dashboard)
and this server deploy to Vercel as **separate projects**. This is the API
project. FastAPI is officially supported on Vercel's Python runtime and
auto-detected from `requirements.txt` — no adapter or `vercel.json` gymnastics
needed (`vercel.json` here only carries the schema reference; no cron is defined).

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
  webhooks and the internal API; a cold email draft may take an extra second or two.

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
   `DASHBOARD_PASS`. Set `RUN_MIGRATIONS_ON_STARTUP=false` and migrate as a
   deploy step (next).
4. **Apply migrations.** The schema is managed by Alembic. Run it as a
   deploy/release step rather than on cold start:
   ```bash
   .venv/bin/python scripts/migrate.py     # = alembic upgrade head
   ```
   Verify against the live DB with `.venv/bin/python scripts/check_neon.py`
   (read-only — lists which expected tables exist and the current revision).
5. **Deploy** — push to the connected branch or `vercel --prod`.
6. **(Recommended)** Put **Cloudflare** (free tier) in front for WAF/DDoS/bot
   mitigation. App-level rate limiting handles the rest. Documented, not built.

## Schema & migrations

The schema is owned by this service and versioned with **Alembic** (`alembic/`,
two migrations). `run_migrations()` applies `alembic upgrade head`; it runs on
startup when `RUN_MIGRATIONS_ON_STARTUP=true` (good for local/dev) and is
otherwise invoked as the deploy step above. It is idempotent, so a database
already at head is a fast no-op. `scripts/seed.py` loads realistic sample data
into the club-domain tables (`people`/`events`/`sponsors`/`finance`).

Neon is the **single source of truth**; the `dsec-app` dashboard reads and writes
these tables directly. This service never serves club-domain data over HTTP.

## Local vs production DB

- Local dev defaults to SQLite (`sqlite:///./local.db`) — zero external services.
- Models are kept DB-agnostic, but **Neon Postgres is the default and supported
  target**. Set `DATABASE_URL` to the Neon pooled string for anything real.

## Tests

A pytest suite lives in `tests/` (dev deps in `requirements-dev.txt`). It uses a
throwaway SQLite DB and never touches Neon or OpenAI. Run it before deploying:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
