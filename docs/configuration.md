# Configuration

All config is loaded by `app/config.py` (pydantic `BaseSettings`) from environment
variables / a local `.env`. In production set these as **Vercel project env vars**;
never commit real secrets. `.env.example` mirrors this table with placeholders.

| Var | Default | Purpose |
|---|---|---|
| `AGENT_SECRET` | `change-me-agent-secret` | Shared secret for the Apps Script → server `X-Agent-Secret` header. |
| `OPENAI_API_KEY` | _(empty)_ | OpenAI auth. Empty → LLM calls raise `LLMError` and the email pipeline degrades to `ignore`. |
| `OPENAI_CLASSIFY_MODEL` | `gpt-4o-mini` | Cheap model for triage. |
| `OPENAI_DRAFT_MODEL` | `gpt-4o-mini` | Model for drafting. |
| `CALCOM_LINK` | `https://cal.com/dsec` | Booking link appended to meeting-type drafts. |
| `SIGNATURE` | `Best regards,\nThe DSEC Committee` | Sign-off appended to drafts. |
| `TONE` | `friendly, concise, and professional` | Drafting tone description. |
| `DASHBOARD_USER` | `admin` | Basic-auth username (dashboard, admin, docs). |
| `DASHBOARD_PASS` | `change-me-dashboard-pass` | Basic-auth password. |
| `DATABASE_URL` | `sqlite:///./local.db` | DB connection. **Use Neon's pooled (pgBouncer) string in prod**, `?sslmode=require`. |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` | Apply `alembic upgrade head` on startup. Handy locally; set **`false`** on serverless and migrate as a deploy step (`scripts/migrate.py`). |
| `API_KEY_PREFIX` | `dsec_live_` | API key token prefix. |
| `RATE_LIMIT_PER_MIN` | `60` | Per-key requests/min. |
| `RATE_LIMIT_PER_IP_PER_MIN` | `120` | Per-IP requests/min (independent of key). |
| `RATE_LIMIT_TRIGGER_PER_DAY` | `200` | Per-key cap on trigger/LLM calls per day. |
| `GLOBAL_DAILY_LLM_CAP` | `1000` | Hard ceiling on total LLM calls/day across all keys. |
| `MAX_REQUEST_BYTES` | `100000` | Request-size guard for public routes. |
| `CRON_SECRET` | _(empty)_ | Reserved/unused. Previously authenticated the Notion reconciliation cron (now removed); kept for a future scheduled job. |
| `DISCORD_WEBHOOK_SECRET` | _(empty)_ | v2 — Discord webhook HMAC. |
| `CALCOM_WEBHOOK_SECRET` | _(empty)_ | v2 — Cal.com webhook HMAC. |

## Notes

- **Empty secrets fail open for stubs in dev.** When a webhook secret is unset,
  `verify_webhook_signature` lets requests through so the v2 stubs are reachable
  locally. Set the secret to enforce verification.
- **Migrations.** With `RUN_MIGRATIONS_ON_STARTUP=true` the app runs
  `alembic upgrade head` on startup (idempotent — a DB already at head is a fast
  no-op). On Vercel, prefer `false` plus a deploy-step `scripts/migrate.py`.
- **Caps are the real money guard.** `RATE_LIMIT_TRIGGER_PER_DAY` and
  `GLOBAL_DAILY_LLM_CAP` are checked *before* any LLM call on trigger routes.
