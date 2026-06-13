# DSEC Agent API

An **extensible** FastAPI integration server for DSEC. v1 ships the **email agent**
(Gmail Apps Script → spam gate → LLM classify + draft → returns a draft, never
auto-sends). The architecture treats email as the first *plugin* among many:
adding Discord, Cal.com, Notion, or any other inbound integration is a new folder
under `app/features/` plus one mount line in `app/main.py` — nothing else changes.

Deploys to **Vercel** (Python / Fluid Compute) backed by **Neon Postgres**.

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
external services. Point it at Neon for production (see below).

---

## Configuration

All config is env-driven via `app/config.py` (pydantic Settings). Copy
`.env.example` → `.env` for local dev; in production set these as **Vercel project
environment variables** (never commit real secrets). Full table in
[`docs/configuration.md`](docs/configuration.md).

Key vars: `AGENT_SECRET`, `OPENAI_API_KEY`, `DATABASE_URL`, `DASHBOARD_USER` /
`DASHBOARD_PASS`, `CRON_SECRET`, and the rate-limit caps.

---

## Endpoints (v1)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness |
| `POST` | `/email/process` | `X-Agent-Secret` | spam gate → classify → draft |
| `POST` | `/admin/keys` | basic auth | mint an API key (raw key shown once) |
| `GET` | `/admin/keys` | basic auth | list keys (never the secret) |
| `POST` | `/admin/keys/{id}/revoke` | basic auth | soft-revoke a key |
| `POST` | `/admin/sync/notion` | basic auth | run the Notion→Neon sync now |
| `GET` | `/admin/sync/notion/cron` | `CRON_SECRET` | Vercel Cron reconciliation sync |
| `GET` | `/public/status` | API key (`read`) | counts + LLM cap status |
| `GET` | `/public/logs` | API key (`read`) | recent EventLog rows |
| `GET` | `/public/events` | API key (`read`) | published events from Neon |
| `POST` | `/public/draft` | API key (`trigger`) | run classify+draft on text |
| `GET` | `/dashboard/` | basic auth | server-rendered audit log |
| `POST` | `/discord/webhook` | HMAC (stub) | **v2** — returns 501 |
| `POST` | `/calcom/webhook` | HMAC (stub) | **v2** — returns 501 |
| `POST` | `/notion/webhook` | handshake + HMAC | verification echo + drives event sync |

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

Scopes: `read` (logs/status/events, no LLM spend) and `trigger` (causes drafts /
LLM spend, counted against the daily caps).

---

## Deploying to Vercel (this server as its own project)

This FastAPI server is a **second Vercel project**, separate from the Next.js
site (dsec.club). Vercel auto-detects FastAPI from `requirements.txt` and the
`app` instance at `app/main.py` — the entire app becomes one Vercel Function.

1. **Create the project.** Import this repo into Vercel as a new project (root
   = this directory). No build config needed; the Python runtime is auto-detected.
2. **Set env vars** (Project → Settings → Environment Variables) — every var from
   `.env.example` with real values. At minimum: `AGENT_SECRET`, `OPENAI_API_KEY`,
   `DATABASE_URL` (Neon pooled), `DASHBOARD_USER`, `DASHBOARD_PASS`, `CRON_SECRET`.
3. **Neon connection string — use the POOLED endpoint.** Serverless functions
   open many short-lived connections; the pooled (pgBouncer) host prevents
   exhausting Neon's connection limit. Example:
   `postgresql+psycopg://USER:PASS@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require`
   SQLAlchemy is configured with a small pool and `pool_pre_ping=True` to survive
   Neon suspending idle compute.
4. **Cron.** `vercel.json` registers a daily reconciliation sync hitting
   `/admin/sync/notion/cron`. Vercel sends `Authorization: Bearer <CRON_SECRET>`;
   set `CRON_SECRET` to match.
5. **Deploy.** Push to the connected branch (or `vercel --prod`).
6. **(Recommended)** Put Cloudflare (free) in front for WAF/DDoS/bot mitigation;
   app-level rate limiting handles the rest.

Full details + constraints in [`docs/deployment.md`](docs/deployment.md).

---

## The three sync triggers (Notion → Neon)

Notion is where the committee edits events; **Neon is what dsec.club reads**
(directly — the site does not go through this API for reads). One sync function,
`sync_notion_events()` in `app/features/events/sync.py`, is invoked three ways:

1. **Notion webhook** (primary, near-real-time) — `POST /notion/webhook`.
2. **Vercel Cron** (daily reconciliation safety net) — `GET /admin/sync/notion/cron`.
3. **Manual admin** (push-it-now / debugging) — `POST /admin/sync/notion`.

The logic lives in exactly one place; the three triggers all call it.

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
- **No persistent in-process state** — all durable state in Neon, scheduling via Cron.
- **One sync implementation, three triggers.**
- **The website reads Neon directly** — FastAPI owns ingest/writes, not reads.
- **A new integration requires zero edits to existing feature folders.**
