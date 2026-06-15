# Configuration

All config is loaded by `app/config.py` (pydantic `BaseSettings`) from environment
variables / a local `.env`. In production set these as **Vercel project env vars**;
never commit real secrets. `.env.example` mirrors this table with placeholders.

| Var | Default | Purpose |
|---|---|---|
| `AGENT_SECRET` | `change-me-agent-secret` | Shared secret for the Apps Script → server `X-Agent-Secret` header. |
| `ANTHROPIC_API_KEY` | _(empty)_ | Anthropic (Claude) auth. Empty → LLM calls raise `LLMError` and the email pipeline degrades to `ignore`. |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude model used for **both** classify (triage) and generate (drafting) — there is no separate classify/draft model. |
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
| `SUPABASE_URL` | _(empty)_ | Supabase project URL for image/attachment Storage. Empty → media uploads return `503`. |
| `SUPABASE_SERVICE_ROLE_KEY` | _(empty)_ | Supabase service-role key (server-side only; bypasses RLS — never expose to the browser). |
| `SUPABASE_STORAGE_BUCKET` | `media` | Name of the public Storage bucket for media. |
| `MEDIA_MAX_UPLOAD_BYTES` | `15000000` | Max source image size (15 MB) for `/media` uploads. |
| `MEDIA_MAX_DIMENSION` | `2000` | Longest-side pixel cap; larger images are downscaled. |
| `ATTACHMENT_MAX_UPLOAD_BYTES` | `25000000` | Max source file size (25 MB) for `/attachments` (PDFs allowed, auto-compressed). |
| `TALLY_API_KEY` | _(empty)_ | Tally key for per-event review forms (server-side only). Empty → `POST .../review-form` returns `503`. |
| `TALLY_API_BASE` | `https://api.tally.so` | Tally API base URL. |
| `CRON_SECRET` | _(empty)_ | Vercel Cron auth for the (optional) daily reconciliation sync. |
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
