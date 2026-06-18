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

## Scopes (API keys & OAuth tokens)

Both `dsec_live_…` API keys and login-issued OAuth access tokens carry **scopes**
that gate the MCP tools (`app/features/mcp/server.py`) and the REST surface. The
scope check is `app/features/mcp/auth.py::has_scope`, which is **backward
compatible** — every credential that already carries the legacy coarse scopes
keeps working unchanged.

### The two scope models

- **Focus-only modules** — events, projects, tasks, people, members, meetings,
  documents, partners. These are **not** isolated at the API: their tools accept
  the legacy coarse `read` / `write`. (Per-module focus is enforced only in the
  dashboard UI, not the API.)
- **Enforced modules** — **Sponsors** and **Finance**. Their tools require a
  **per-module scope** so a credential can be granted exactly that module without
  blanket access:

  | tool group | required scope |
  | --- | --- |
  | sponsors read (`list_sponsors`, `list_sponsor_contacts`, `list_sponsor_packages`, `list_sponsor_leads`) | `read:sponsors` |
  | sponsors write (`create_sponsor`, `update_sponsor`, contact/package/lead writes) | `write:sponsors` |
  | finance read (`finance_summary`, `list_transactions`) | `read:finance` |
  | finance write (`set_event_budget`) | `write:finance` |

  (The event line-up tools `list/link/unlink_event_sponsor` stay on coarse
  `read`/`write` — they belong to the Events module, not Sponsors.)

### Scope algebra (`has_scope`)

- legacy `write` ⊇ every `write:*`, every `read:*`, and legacy `read`.
- legacy `read` ⊇ every `read:*`.
- `write:X` satisfies `read:X`.
- any other scope (`trigger`, `ingest`, an exact module scope) matches only
  itself. A pure module key (e.g. `read:events`) is **not** a legacy `read`, so
  it cannot reach the broad tools — and crucially `read:events` does **not**
  satisfy `read:sponsors`/`read:finance`.

### How a login is scoped (OAuth)

`oauth/service.py::scopes_for_grant` derives the issued token's scope string from
the user's **role modules** (`app_role` via `app_user.role_id`):

- each granted READ module → `read:<module>`, each WRITE module → `write:<module>`;
- the **enforced** modules (sponsors, finance) are represented **exclusively** by
  those per-module scopes — a role without the module **never** receives
  `*:sponsors` / `*:finance`;
- the **focus-only** modules additionally yield the legacy `read` / `write` so
  the broad tools keep working;
- an `admin` role is a superuser → the full module universe;
- if the `app_role` RBAC tables are absent (e.g. the SQLite test DB) it falls
  back to the unchanged coarse grant.

The net effect: a role whose only modules are enforced ones (e.g. a Treasurer
with just Finance) gets a token like `read:finance write:finance` and **cannot**
reach Sponsors (or any focus tool). Note that a credential that *does* hold the
legacy `read`/`write` (an existing key, an admin, or any role with a focus-only
module) still reaches the enforced tools by design — full per-focus-module
isolation is a later phase.

### Self-service minting

`POST /admin/keys/self` accepts the per-module scopes too (`VALID_SCOPES` in
`app/core/apikeys.py`). The escalation guard is `has_scope`-aware, so a broad
service key (legacy `write`) can mint a narrower per-module key (`write:sponsors`)
but never the reverse.

## Rate limiting & error codes

| Code | Meaning |
|---|---|
| `401` | missing/invalid auth (agent secret, basic auth, or API key) |
| `403` | API key lacks a required scope |
| `429` | rate limit hit — per-IP, per-key, per-key daily trigger, or global LLM cap. Includes `Retry-After`. |
| `422` | request validation failed |
| `501` | v2 webhook stub |
