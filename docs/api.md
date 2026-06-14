# API reference

All error responses are clean JSON (`{"detail": "..."}`) — no stack traces leak
to callers (centralised exception handling in `app/main.py`).

OpenAPI docs are served at `/docs` and `/openapi.json` but **gated behind basic
auth** so the API surface isn't public.

---

## `GET /health`

No auth. Liveness probe.

```json
{ "status": "ok" }
```

---

## Email — `POST /email/process`

Auth: `X-Agent-Secret: <AGENT_SECRET>` header. Used by the Gmail Apps Script.

**Request**

```json
{
  "threadId": "string",
  "messageId": "string",
  "from": "string",
  "to": "string",
  "subject": "string",
  "body": "string",
  "date": "ISO8601 string"
}
```

**Response**

```json
{ "action": "draft", "draftBody": "..." }
```
or
```json
{ "action": "ignore" }
```

Pipeline: spam gate (no LLM) → classify (`needs-meeting` / `simple-reply` /
`fyi-no-reply`) → draft. `needs-meeting` drafts append `CALCOM_LINK` instead of
proposing times. Any failure → `{"action":"ignore"}` (never 500).

---

## Admin — key management

Auth: HTTP basic auth (`DASHBOARD_USER` / `DASHBOARD_PASS`). Internal only.

### `POST /admin/keys`

```json
{ "name": "Ranveer script", "scopes": ["read", "trigger"] }
```
→ returns the raw key **once**:
```json
{ "id": 1, "name": "...", "prefix": "dsec_live_a1b2c3d4", "scopes": ["read","trigger"], "raw_key": "dsec_live_…" }
```

### `GET /admin/keys`
Lists keys — prefix, name, scopes, `created_at`, `last_used_at`, `revoked`.
Never the secret.

### `POST /admin/keys/{id}/revoke`
Soft-revoke (the row is kept for the audit trail).

---

## Public API

Auth: API key via `Authorization: Bearer <key>` or `X-API-Key`. Every route is
IP- and key-rate-limited.

### `GET /public/status` — scope `read`
```json
{ "status":"ok", "log_count": 42, "llm_calls_today": 3, "global_daily_cap": 1000 }
```

### `GET /public/logs?source=&action=&limit=` — scope `read`
Recent `EventLog` rows (max `limit=200`), newest first.

### `POST /public/draft` — scope `trigger`
Runs classify+draft on supplied text. **Checked against the per-key daily trigger
cap and the global daily LLM cap before any LLM work.**

```json
{ "subject": "...", "from": "...", "body": "..." }
```
→ `{ "action": "draft", "draftBody": "..." }` or `{ "action": "ignore" }`.

`POST /public/notify` (relay to Discord) is reserved for v2.

---

## Webhooks (v2 scaffolding)

### `POST /discord/webhook`
HMAC-guarded stub. Returns `501 {"detail":"discord webhook not yet implemented"}`.

### `POST /calcom/webhook`
HMAC-guarded stub. Returns `501 {"detail":"calcom webhook not yet implemented"}`.

---

## Dashboard — `GET /dashboard/`

Auth: HTTP basic auth. Server-rendered HTML audit log of recent `EventLog` rows,
filterable by `source` and `action`.

---

## Rate limiting & error codes

| Code | Meaning |
|---|---|
| `401` | missing/invalid auth (agent secret, basic auth, or API key) |
| `403` | API key lacks a required scope |
| `429` | rate limit hit — per-IP, per-key, per-key daily trigger, or global LLM cap. Includes `Retry-After`. |
| `422` | request validation failed |
| `501` | v2 webhook stub |
