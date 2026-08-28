# Cutover — moving `api.dsec.club` from Vercel to the VPS

The step-by-step for the switch itself. Background and rationale live in
[`vps-deployment.md`](vps-deployment.md); this is the operational sequence.

**Total user-visible downtime: none, if the order below is followed.** The API
keeps serving from Vercel until DNS moves, and Vercel stays deployed afterwards
as a one-record rollback.

---

## Before you start

| | |
|---|---|
| VPS | `51.161.130.240` (`vps-8edf3981.vps.ovh.ca`), Sydney |
| SSH | `ssh dsec@51.161.130.240` — key only, no password |
| App dir | `~/dsec-api` on the box |
| DNS | Cloudflare (`rose`/`kolton.ns.cloudflare.com`) |
| Current record | `api.dsec.club` CNAME → `606dd6d52ec2b90c.vercel-dns-017.com` |

Confirm the box is healthy on its own hostname first. Everything below assumes
this returns 200:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://vps-8edf3981.vps.ovh.ca/health
```

---

## 1. Lower the TTL — do this at least an hour ahead

In Cloudflare → `dsec.club` → **DNS** → the `api` record → set **TTL = Auto**
(Cloudflare's minimum, 300 s) or **1 min** if offered.

This is the only step with a lead time. Skipping it means resolvers may cache the
old Vercel answer for however long the current TTL says, and rollback would be
just as slow.

## 2. Switch the DNS record

Still in Cloudflare → **DNS** → edit the `api` record:

| Field | Value |
|---|---|
| Type | **A** (was CNAME) |
| Name | `api` |
| IPv4 address | `51.161.130.240` |
| Proxy status | **DNS only — grey cloud** |
| TTL | Auto |

**The grey cloud matters.** `api.dsec.club` is DNS-only today (`server: Vercel`,
no `cf-ray`), and it must stay that way. Turning on the orange cloud makes the
connecting peer a Cloudflare edge IP, so every visitor collapses into a handful
of rate-limit buckets and the per-IP limit stops distinguishing anyone. If you
ever do want Cloudflare in front, Caddy first needs `trusted_proxies` for
Cloudflare's ranges and must read `CF-Connecting-IP` instead — see the comment in
`Caddyfile`.

Delete the old CNAME if Cloudflare does not replace it automatically. There must
be exactly one `api` record.

## 3. Point Caddy at the domain — immediately after step 2

```bash
ssh dsec@51.161.130.240
cd ~/dsec-api
sed -i 's|^API_DOMAIN=.*|API_DOMAIN=api.dsec.club|' .env
docker compose up -d --force-recreate caddy
docker compose logs -f caddy        # watch for "certificate obtained successfully"
```

Caddy requests the certificate on the first request to the new hostname, using
an HTTP-01 challenge on port 80 — which only succeeds once DNS points here.
**That is why this step comes after the DNS change, not before.** Doing it early
burns Let's Encrypt's failed-validation allowance (5 per hostname per hour) and
can delay issuance by up to an hour.

Expect the certificate within a minute of DNS propagating.

## 4. Verify

```bash
# certificate is for the right host and issued by Let's Encrypt
echo | openssl s_client -connect api.dsec.club:443 -servername api.dsec.club 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# the API answers, and answers from the VPS (no x-vercel-id header)
curl -sI https://api.dsec.club/health | grep -iE '^server|^x-vercel-id'
curl -s https://api.dsec.club/website/events | head -c 120

# the four front-ends still work
for u in https://dsec.club https://app.dsec.club https://hub.dsec.club; do
  printf '%-26s %s\n' "$u" "$(curl -sL -o /dev/null -w '%{http_code}' "$u")"
done
```

A response with **no `x-vercel-id` header** is the signal you are on the VPS.

## 5. Leave Vercel deployed for a week

Do **not** delete the `dsec-api` Vercel project on cutover day. It costs nothing
to leave, and it is the entire rollback plan. Revisit after a week of clean logs.

---

## Rollback

One record. In Cloudflare, change the `api` record back to:

| Field | Value |
|---|---|
| Type | CNAME |
| Target | `606dd6d52ec2b90c.vercel-dns-017.com` |
| Proxy | DNS only |

At a 300 s TTL this takes effect in about five minutes. Nothing on the VPS needs
stopping — it simply stops receiving traffic. No data is involved either way:
both deployments talk to the same Neon database.

---

## Retiring Vercel, later

Once you have run a week on the VPS with no surprises:

1. Confirm nothing else points at the Vercel deployment (`vercel.json` `crons`
   are already replaced by the `cron` container).
2. Remove the `api.dsec.club` domain from the Vercel project **before** deleting
   the project, so Vercel stops answering for it.
3. Delete the project.

Keep `docs/deployment.md` — it documents the Vercel setup and is the reference if
this ever has to be reversed properly.

---

## Operating it afterwards

```bash
cd ~/dsec-api
docker compose ps                     # health
docker compose logs -f api            # tail
git pull && docker compose up -d --build   # deploy a change
docker compose run --rm migrate       # migrate without restarting the API
docker compose restart api            # restart just the app
```

**Keep the `caddy_data` volume.** It holds the certificates; destroying it
re-issues on every deploy and will hit Let's Encrypt's rate limits.

`sudo` does not work for the `dsec` user — it was created with
`--disabled-password`, so there is no password to authenticate with. Docker needs
no `sudo` (the user is in the `docker` group), but anything that genuinely
requires root must be done as `ubuntu`, which has passwordless `sudo`.

### Backups — not yet configured

`deploy/backup-neon.sh` exists but is **not installed**. Neon has its own
point-in-time recovery, so this is a second line of defence rather than the only
one, but it should be set up:

```bash
ssh ubuntu@51.161.130.240
sudo cp /home/dsec/dsec-api/deploy/backup-neon.sh /usr/local/bin/dsec-backup
sudo chmod +x /usr/local/bin/dsec-backup
sudo crontab -e
#  15 3 * * *  DATABASE_URL='postgresql://…' BACKUP_REMOTE='gdrive:dsec-backups' /usr/local/bin/dsec-backup
```

**Set `BACKUP_REMOTE`.** Without it the backup survives loss of the database but
not loss of the VPS, which is half the point.
