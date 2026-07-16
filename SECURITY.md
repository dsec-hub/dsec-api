# DSEC — Rate Limiting & DDoS / Abuse Protection

Defence in layers. No single layer stops everything, so each domain
(`dsec.club`, `app.dsec.club`, `api.dsec.club`) sits behind all of them.

```
            ┌─────────────────────────────────────────────────────┐
 Internet → │ 1. Cloudflare DNS (authoritative, not proxied)       │  ← grey-cloud / DNS only
            ├─────────────────────────────────────────────────────┤
            │ 2. Vercel Firewall (platform DDoS + custom rules)    │  ← blocks volumetric DDoS (the edge)
            ├─────────────────────────────────────────────────────┤
            │ 3. App code (Upstash per-IP + login throttle)        │  ← scripting / brute-force
            │    dsec-api: per-key + per-IP + daily LLM caps (Neon) │
            └─────────────────────────────────────────────────────┘
```

> **Why layers:** a true volumetric DDoS *cannot* be stopped in application
> code — by the time your function runs, you've already paid for the request
> (and, for `dsec-api`, touched Neon). Floods must die at the **edge** — which
> here is **Vercel Firewall (layer 2)**, since the Vercel records are grey-cloud
> (DNS-only) in Cloudflare and Cloudflare's proxied edge protections therefore
> don't sit in front of the apps (see Layer 1). App code (layer 3) is for what
> the edge is bad at: credential stuffing on the login form and scripted
> hammering of an authenticated session.

---

## Layer 3 — Application code (DONE, in this repo)

### dsec-app (Next.js)

Implemented and live as soon as the Upstash env vars are set:

| What | Where | Limit |
|---|---|---|
| Per-IP throttle on every page/route | `src/proxy.ts` → `src/lib/rate-limit.ts` | 120 req / 60 s / IP |
| Login brute-force / credential stuffing | `src/auth.ts` `authorize()` | 8 attempts / 60 s / (IP + email) |

- State lives in **Upstash Redis** — the only store that counts accurately
  across Vercel's many short-lived function instances.
- **Fails open:** if `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` are
  unset, nothing is throttled (so local dev and the current prod keep working
  until you wire Upstash up). Set them in production.
- Login excludes the proxy matcher (`/api/auth/*`), which is why the throttle
  lives inside `authorize()` instead.

### dsec-api (FastAPI) — already had this

`app/core/ratelimit.py` (`NeonRateLimiter`) enforces, per authed route:
per-key/min, per-IP/min, per-key daily `trigger` cap, and a global daily LLM
cap. Tunables in `app/config.py` (`RATE_LIMIT_*`, `GLOBAL_DAILY_LLM_CAP`,
`MAX_REQUEST_BYTES`). No change needed; the edge layers below cover the gaps
(e.g. `/health`) without adding a per-request Neon write.

---

## Layer 1 — Cloudflare (DNS — already set up)

Cloudflare is the **authoritative DNS** for all three domains; the nameservers
**already point to Cloudflare** (see [`HOSTING.md`](./HOSTING.md) → Stage 5) and
**must not be moved** — Hostinger only hosts the mailboxes. There is no nameserver
migration to do.

Per HOSTING.md (authoritative), the Vercel web records (`@`, `www`, `app`, `api`)
are **grey-cloud / "DNS only"**, *not* proxied (orange). Proxying Cloudflare in
front of Vercel breaks cert issuance / domain verification and can cause redirect
loops, so the records stay grey-cloud and **Vercel serves TLS directly**.

> ⚠️ **Consequence:** because the Vercel records are grey-cloud, Cloudflare is
> **not in the request path** for the apps — it only answers DNS. Cloudflare's
> proxied-only protections (**WAF custom rules**, **Rate limiting rules**, **Bot
> Fight Mode**, the SSL/TLS proxy mode) therefore **do not apply** to the Vercel
> apps. Edge DDoS/abuse mitigation for them is carried by **Vercel Firewall
> (Layer 2)** plus the app code (Layer 3).

What Cloudflare *does* contribute without proxying:

1. **Authoritative DNS** — already configured. Keep the Vercel web records
   **DNS only (grey cloud)** and leave the Hostinger `MX` / SPF / DKIM / DMARC
   records in place (also grey). All records are edited in the Cloudflare dashboard.
2. **Turnstile** (optional, recommended for the public site) — a free captcha on
   the sponsor/contact forms. It's a script + token check, so it works regardless
   of the grey-cloud setting. HOSTING.md lists the `TURNSTILE_*` env vars.

> If you ever wanted Cloudflare's edge WAF / rate-limiting / Bot Fight Mode in
> front of the apps, you'd have to **proxy** (orange-cloud) the records — which
> HOSTING.md rules out for Vercel. Use **Vercel Firewall (Layer 2)** for edge
> rate-limiting instead (it covers `/api/auth` and `api.dsec.club` below).

## Layer 2 — Vercel Firewall (you set this up — no keys)

Native to your hosting, no account/keys needed — and because the Vercel records
are grey-cloud, this is the **primary edge layer** for the apps (all web traffic
reaches Vercel directly). For **each** of the 3 Vercel projects:

1. Project → **Firewall**. Vercel's **Attack Challenge Mode** + automatic DDoS
   mitigation are on by default — confirm they're enabled.
2. Add **Custom Rules** — this is where the per-path rate limits live (Cloudflare
   isn't proxying, so it can't do them):
   - **Rate limit** rule: `100 requests / 60 s` per IP → **Challenge**.
   - Stricter rule on `app.dsec.club` path `/api/auth/*`: `20 / 60 s` → **Deny**.
   - On the API project, a rate-limit rule on `api.dsec.club/*` → **Challenge**.
3. (Hardening) Optionally **deny** direct traffic to the raw `*.vercel.app` origin
   so the public can only reach each app through its **custom domain**.

---

## What I need from you to finish

Provide these and I'll plug them in / verify (or add them yourself in the
Vercel dashboard env settings for each project):

1. **Upstash** (required for layer 3 to actually throttle):
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`
   - Get them free at <https://console.upstash.com> → Create Database (Redis) →
     REST API section. Add both to the **dsec-app** Vercel project env (Production
     + Preview) and your local `.env.local`.

2. **Cloudflare** (DNS — already configured): nothing is required to *enable*
   protection here, because the Vercel records are grey-cloud, so Cloudflare's
   WAF / rate limiting don't apply (see Layer 1). If you want me to script DNS
   changes via the API I'll need a **Cloudflare API token** scoped to Zone →
   *DNS:Edit* for the `dsec.club` zone, plus the **Zone ID** (Cloudflare dashboard
   → Overview, right sidebar) — otherwise edit records in the dashboard. Optionally
   enable **Turnstile** on the public forms (`TURNSTILE_*` env vars).

3. **Vercel Firewall** (layer 2): dashboard-only, nothing for me to receive.

---

## Tuning the limits

- App per-IP / login limits: edit the `Ratelimit.slidingWindow(...)` calls in
  `dsec-app/src/lib/rate-limit.ts`.
- API limits: `dsec-api/app/config.py`.
- Start conservative (the numbers above suit committee scale) and loosen if you
  see false-positive 429s for legitimate users.
