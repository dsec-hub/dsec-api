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
| `API_KEY_PREFIX` | `dsec_live_` | API key token prefix. |
| `RATE_LIMIT_PER_MIN` | `60` | Per-key requests/min. |
| `RATE_LIMIT_PER_IP_PER_MIN` | `120` | Per-IP requests/min (independent of key). |
| `RATE_LIMIT_TRIGGER_PER_DAY` | `200` | Per-key cap on trigger/LLM calls per day. |
| `GLOBAL_DAILY_LLM_CAP` | `1000` | Hard ceiling on total LLM calls/day across all keys. |
| `MAX_REQUEST_BYTES` | `100000` | Request-size guard for public routes. |
| `CRON_SECRET` | _(empty)_ | Bearer token Vercel Cron sends to `/admin/sync/notion/cron`. |
| `DISCORD_WEBHOOK_SECRET` | _(empty)_ | v2 — Discord webhook HMAC. |
| `CALCOM_WEBHOOK_SECRET` | _(empty)_ | v2 — Cal.com webhook HMAC. |
| `NOTION_WEBHOOK_SECRET` | _(empty)_ | v2 — Notion verification token (for `X-Notion-Signature`). |
| `NOTION_API_KEY` | _(empty)_ | v2 — Notion API auth for the events sync. |
| `NOTION_EVENTS_DATABASE_ID` | _(empty)_ | v2 — Notion events database to mirror. |

## Notes

- **Empty secrets fail open for stubs in dev.** When a webhook secret is unset,
  `verify_webhook_signature` lets requests through so the v2 stubs are reachable
  locally. Set the secret to enforce verification.
- **Empty Notion config = no-op sync.** `sync_notion_events()` skips fetching when
  `NOTION_API_KEY` / `NOTION_EVENTS_DATABASE_ID` are unset, so the sync routes
  work (and log) without erroring before Notion is wired in v2.
- **Caps are the real money guard.** `RATE_LIMIT_TRIGGER_PER_DAY` and
  `GLOBAL_DAILY_LLM_CAP` are checked *before* any LLM call on trigger routes.
