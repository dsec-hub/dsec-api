# Deployment — OVH VPS + Neon

How `dsec-api` runs on its own server. The Vercel setup this replaces is still
documented in [`deployment.md`](deployment.md); that stays accurate until the DNS
cutover, and is the rollback target afterwards.

## Why the move

The Vercel deployment executed in **`iad1` (Washington DC)** while Neon lives in
**`ap-southeast-2` (Sydney)**. Every database round trip crossed the Pacific
twice. Measured against the live deploy:

| | Vercel (`iad1`) | Local, same code | Expected on VPS (Sydney) |
|---|---|---|---|
| `/health` (no DB) | 320–490 ms | 0.6 ms | ~20 ms |
| `/website/events` | **2.7–2.9 s** | 1.6 ms | ~20–50 ms |

Six consecutive warm requests all landed at ~2.7 s, so this was structural, not
cold-start noise. Co-locating the app with the database in Sydney is the whole
point of the move; the lower bill is a side effect.

A persistent process also unlocks things serverless cannot host at all — most
immediately a **gateway** Discord bot (an open WebSocket), where the current
`/discord/interactions` webhook bot is the only shape Vercel could support.

## Shape

```
Internet ──▶ Caddy (TLS, :443) ──▶ api (uvicorn, :8000) ──▶ Neon (ap-southeast-2)
                                    ▲                        Supabase (media)
                              cron ─┘  monthly games draw
```

Four containers, one `compose.yaml`:

| Service | Role |
|---|---|
| `migrate` | runs `alembic upgrade head` once, **must exit 0 before `api` starts** |
| `api` | the FastAPI app, one uvicorn worker, no published host port |
| `caddy` | TLS termination, automatic Let's Encrypt, reverse proxy |
| `cron` | replaces the `crons` block in `vercel.json` |

`api` deliberately publishes **no host port** — only Caddy can reach it. Binding
`8000` on the host would serve the API unencrypted and bypass the proxy that
makes per-IP rate limiting trustworthy (see below).

## Sizing

Measured, not estimated:

| | |
|---|---|
| App imported, steady | **136 MB RSS** |
| Peak during 12 MP image resize | **227 MB** |
| Running container | **127 MiB** |
| Everything incl. Caddy, cron, OS, a future gateway bot | **~1.1 GB** |
| OVH VPS-1 provides | **4 GB / 2 vCPU / 40 GB** |

Roughly a quarter of the box. One uvicorn worker is correct: 188 of the ~202
endpoints are sync `def`, which Starlette runs on a 40-thread pool inside a
single process, so concurrency comes from threads and DB connections — not from
worker processes. Add workers only if a measurement shows CPU is the limit; the
connection pool binds first.

**Do not self-host Postgres on this box.** Neon stays the source of truth:
`dsec-hub` writes it directly via Drizzle, and a single VPS with hand-rolled
backups is how a club loses a year of data.

## First deploy

```bash
# 1. Harden the fresh box + install Docker (run once, as root)
ssh root@<vps-ip> 'bash -s' < deploy/bootstrap-vps.sh "$(cat ~/.ssh/id_ed25519.pub)"

# 2. Verify key login in a SECOND terminal before closing the first
ssh dsec@<vps-ip>

# 3. Deploy
git clone https://github.com/dsec-hub/dsec-api.git && cd dsec-api
cp .env.production.example .env     # fill in real values
docker compose up -d --build
docker compose logs -f api
```

`bootstrap-vps.sh` creates a `dsec` user, installs your key, disables root and
password SSH (**only** if a key is present — it will not lock you out), enables
`ufw` (22/80/443), `fail2ban`, unattended security upgrades, Docker, and 2 GB of
swap as an OOM cushion.

### Cutover order

Do not touch DNS until the box serves real traffic on its own address.

1. Deploy with `API_DOMAIN` set to a throwaway hostname; confirm `/health`,
   `/website/events` and one authenticated route.
2. Lower the `api.dsec.club` TTL to 60 s **at least an hour ahead**.
3. Point `api.dsec.club` at the VPS. Caddy issues the certificate on first hit.
4. Update the Discord **Interactions Endpoint URL** in the Developer Portal —
   it is validated on save, so the new host must already be live.
5. Verify all four front-ends, then keep the Vercel project alive for a week.

Rollback is one DNS record.

## What changed in the app

Four changes were needed to run safely off Vercel. All are in
`chore/vps-migration-prep`.

**`validate_production_settings()` keyed off `VERCEL=1`.** That check refuses to
boot with placeholder secrets or a SQLite database — and it would have silently
stopped running the moment the app left Vercel, exactly when it started to
matter. It now triggers on `APP_ENV=production` (set by compose) as well.

**Connection pool 5 + 2 → 15 + 5** (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`). The old
value was right for serverless, where N ephemeral instances each held a pool and
the danger was exhausting Neon's limit. In one long-lived process it is instead
the throughput ceiling, with 40 threads queueing on 5 connections.

**Authenticated traffic is no longer charged to the per-IP bucket.** All four
front-ends call this API from server-side code and egress from very few
addresses, so a shared 120 req/min per-IP bucket meant they 429'd each other in a
way that looked random. Each holds its own key, so the per-key limit is both the
correct control and a tighter one. The per-key default moved 60 → 300/min; the
per-IP limit still guards the unauthenticated surface.

**`X-Real-IP` must be set by the proxy.** `app/core/net.py` trusts that header
because Vercel's edge set it and clients could not override it. A self-hosted
proxy only recreates that guarantee if it overwrites the header — hence
`header_up X-Real-IP {remote_host}` in the `Caddyfile`. Without it an attacker
sends a fresh fake `X-Real-IP` per request, gets a new rate-limit bucket each
time, and per-IP limiting stops working. **If Cloudflare is ever put in front
(orange cloud), the connecting peer becomes a Cloudflare edge IP** and every
visitor collapses into a few buckets; Caddy then needs `trusted_proxies` for
Cloudflare's ranges and should read `CF-Connecting-IP` instead.

## Backups

```bash
sudo cp deploy/backup-neon.sh /usr/local/bin/dsec-backup && sudo chmod +x $_
sudo crontab -e
#  15 3 * * *  DATABASE_URL='postgresql://...' BACKUP_REMOTE='gdrive:dsec-backups' /usr/local/bin/dsec-backup
```

Nightly `pg_dump`, gzipped, 14-day retention, aborts rather than rotating if the
dump looks empty. **Set `BACKUP_REMOTE`** — without it the backup survives loss
of the database but not loss of the VPS, which is half the point.

## Operations

```bash
docker compose logs -f api            # tail
docker compose ps                     # health
docker compose up -d --build          # deploy a change
docker compose run --rm migrate       # migrate without restarting the API
docker compose down                   # stop (keeps cert + config volumes)
```

Keep the `caddy_data` volume. It holds the TLS certificates; destroying it
re-issues on every deploy and will hit Let's Encrypt rate limits. To rehearse the
cutover, uncomment `acme_ca` (staging) in the `Caddyfile` first.
