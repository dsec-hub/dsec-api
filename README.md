# DSEC Agent API

An **extensible** FastAPI integration server for DSEC. v1 ships the **email agent**
(Gmail Apps Script → spam gate → LLM classify + draft → returns a draft, never
auto-sends). The architecture treats email as the first *plugin* among many:
adding Discord, Cal.com, or any other inbound integration is a new folder
under `app/features/` plus one mount line in `app/main.py` — nothing else changes.

Deploys to **Vercel** (Python / Fluid Compute) backed by **Neon Postgres**.

**Neon is the single source of truth** for the club's data. The internal exec
dashboard — `dsec-app`, a separate NextAuth-gated Next.js app (not in this repo) —
reads and writes Neon directly. `dsec-api` **owns the schema** (SQLAlchemy models
in `app/models.py` + Alembic migrations) but deliberately does **not** expose that
club-domain data over HTTP: it is the email/automation layer, not a data API.

---

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit values
uvicorn app.main:app --reload
```

- App: <http://127.0.0.1:8000>
- Health: <http://127.0.0.1:8000/health>
- Dashboard: <http://127.0.0.1:8000/dashboard/> (basic auth: `DASHBOARD_USER` / `DASHBOARD_PASS`)
- API docs: <http://127.0.0.1:8000/docs> (also behind basic auth — the surface is not public)

The default `DATABASE_URL` is SQLite (`./local.db`) so you can run with zero
external services. Point it at Neon for production (see below). On startup the app
applies Alembic migrations automatically (`RUN_MIGRATIONS_ON_STARTUP=true`), so the
schema is created on first run; load sample data into the domain tables with
`.venv/bin/python scripts/seed.py`, and run the tests with
`.venv/bin/python -m pytest`.

---

## Configuration

All config is env-driven via `app/config.py` (pydantic Settings). Copy
`.env.example` → `.env` for local dev; in production set these as **Vercel project
environment variables** (never commit real secrets). Full table in
[`docs/configuration.md`](docs/configuration.md).

Key vars: `AGENT_SECRET`, `OPENAI_API_KEY`, `DATABASE_URL`, `DASHBOARD_USER` /
`DASHBOARD_PASS`, `RUN_MIGRATIONS_ON_STARTUP`, and the rate-limit caps.

---

## Endpoints (v1)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness |
| `POST` | `/email/process` | `X-Agent-Secret` | spam gate → classify → draft |
| `POST` | `/admin/keys` | basic auth | mint an API key (raw key shown once) |
| `GET` | `/admin/keys` | basic auth | list keys (never the secret) |
| `POST` | `/admin/keys/{id}/revoke` | basic auth | soft-revoke a key |
| `GET` | `/public/status` | API key (`read`) | counts + LLM cap status |
| `GET` | `/public/logs` | API key (`read`) | recent EventLog rows |
| `POST` | `/public/draft` | API key (`trigger`) | run classify+draft on text |
| `GET` | `/dashboard/` | basic auth | server-rendered audit log |
| `POST` | `/discord/webhook` | HMAC (stub) | **v2** — returns 501 |
| `POST` | `/calcom/webhook` | HMAC (stub) | **v2** — returns 501 |

See [`docs/api.md`](docs/api.md) for request/response shapes.

---

## Mint your first API key

The public API is API-key authenticated. Keys are created **only** via the
internal admin router (basic auth), never self-serve. The raw key is shown
**exactly once** — store it immediately; a lost key is revoked + reissued.

```bash
curl -u "$DASHBOARD_USER:$DASHBOARD_PASS" \
  -X POST http://127.0.0.1:8000/admin/keys \
  -H 'content-type: application/json' \
  -d '{"name":"Ranveer script","scopes":["read","trigger"]}'
# -> {"id":1,"prefix":"dsec_live_a1b2c3d4","raw_key":"dsec_live_…","scopes":[...]}
```

Use it:

```bash
curl http://127.0.0.1:8000/public/status \
  -H "Authorization: Bearer dsec_live_…"
```

Scopes: `read` (logs/status, no LLM spend) and `trigger` (causes drafts / LLM
spend, counted against the daily caps).

---

## Deploying to Vercel (this server as its own project)

This FastAPI server is its **own Vercel project**, separate from the club's
Next.js front-ends (including the internal `dsec-app` exec dashboard). Vercel
auto-detects FastAPI from `requirements.txt` and the `app` instance at
`app/main.py` — the entire app becomes one Vercel Function.

1. **Create the project.** Import this repo into Vercel as a new project (root
   = this directory). No build config needed; the Python runtime is auto-detected.
2. **Set env vars** (Project → Settings → Environment Variables) — every var from
   `.env.example` with real values. At minimum: `AGENT_SECRET`, `OPENAI_API_KEY`,
   `DATABASE_URL` (Neon pooled), `DASHBOARD_USER`, `DASHBOARD_PASS`.
3. **Neon connection string — use the POOLED endpoint.** Serverless functions
   open many short-lived connections; the pooled (pgBouncer) host prevents
   exhausting Neon's connection limit. Example:
   `postgresql+psycopg://USER:PASS@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require`
   SQLAlchemy is configured with a small pool and `pool_pre_ping=True` to survive
   Neon suspending idle compute.
4. **Migrations.** Apply the schema as a deploy step with
   `.venv/bin/python scripts/migrate.py` (or `alembic upgrade head`) and set
   `RUN_MIGRATIONS_ON_STARTUP=false` so cold starts don't run migrations. Leaving
   it `true` lets the app self-migrate on startup — convenient, but a deploy-step
   migration is preferred on serverless.
5. **Deploy.** Push to the connected branch (or `vercel --prod`).
6. **(Recommended)** Put Cloudflare (free) in front for WAF/DDoS/bot mitigation;
   app-level rate limiting handles the rest.

Full details + constraints in [`docs/deployment.md`](docs/deployment.md).

---

## Data model — Neon is the single source of truth

The club's operational data lives in Neon and is edited through `dsec-app` (the
NextAuth-gated exec dashboard), which reads and writes the tables directly — full
CRUD, no API in between. `dsec-api` **owns** that schema and nothing else does:

- **Operational tables** — `event_log`, `api_key`, `rate_limit`. Used by this
  service (audit log, public API keys, rate limiter).
- **Club-domain tables** — `people`, `events`, `sponsors`, `finance`, with FK
  relations between them and `created_at` / `updated_at` / `archived`
  (soft-delete) columns on every row. These are the dashboard's source of truth.

Schema changes are versioned with **Alembic** (`alembic/`, applied via
`scripts/migrate.py`). `dsec-api` does not serve club-domain data over HTTP — it
is the email/automation layer that happens to own the schema.

> _History: an earlier scaffold mirrored events from Notion into Neon on a sync
> schedule. That Notion integration has been removed — Neon is now authoritative._

---

## Adding a new feature module

The whole point of the architecture. To add an integration (say, Slack):

1. `mkdir app/features/slack` with an `__init__.py` and a `router.py` exposing
   `router = APIRouter()`.
2. Reuse the shared core — `app.auth`, `app.db`, `app.core.llm`,
   `app.core.logging`, `app.core.apikeys`, `app.core.ratelimit`. Don't
   reimplement them.
3. Add **one** line to `app/main.py`:
   `app.include_router(slack_router, prefix="/slack", tags=["slack"])`.

That's it. No existing feature folder is touched. See
[`docs/extending.md`](docs/extending.md) for a worked example.

---

## Docs

- [`docs/architecture.md`](docs/architecture.md) — structure, core modules, request flow
- [`docs/api.md`](docs/api.md) — endpoint reference & schemas
- [`docs/configuration.md`](docs/configuration.md) — every env var
- [`docs/deployment.md`](docs/deployment.md) — Vercel + Neon, serverless constraints
- [`docs/extending.md`](docs/extending.md) — adding a new feature module
- [`CHANGELOG.md`](CHANGELOG.md) · [`TODO.md`](TODO.md)

---

## Hard rules (enforced by design)

- **Never auto-send email** — the server only ever returns draft text.
- **Spam gate runs before any LLM call** — the cost guard, no exceptions.
- **Never store raw API keys** — argon2 hash only, shown once at creation.
- **Trigger routes check rate limit + global LLM cap before any work.**
- **Every feature shares core** — no feature reimplements db/auth/llm/logging.
- **No persistent in-process state** — all durable state lives in Neon.
- **Neon is the single source of truth** — `dsec-app` reads/writes it directly;
  `dsec-api` owns the schema but never serves club-domain data over HTTP.
- **A new integration requires zero edits to existing feature folders.**
