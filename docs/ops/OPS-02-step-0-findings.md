# OPS-02 — Step 0 findings (2026-08-27)

Nothing has been merged, pushed, deployed, or deleted. Every command below was
read-only, except a throwaway clone under a scratch directory.

`curl -s https://api.dsec.club/health` → **200 `{"status":"ok"}`** (checked at start).
`dig +short api.dsec.club` → `<VPS_IP>`. Response carries `via: 1.1 Caddy`
and **no `x-vercel-id`** — production is the VPS, as `docs/cutover.md` claims.

---

## Three findings that change the plan

### 1. PR #3 is not the branch production is running

The ticket says "PR #3 (`deploy/vps`)". It isn't.

| PR | head branch | title |
|---|---|---|
| #1 | `ci/pytest` | Run the test suite on every push and PR |
| #2 | `fix/rest-scope-algebra` | Make per-module API scopes actually work, and use them |
| #3 | **`chore/vps-migration-prep`** | Prepare the API to run on its own VPS |

`deploy/vps` has **never had a pull request** (`state=all` returns exactly three PRs).

`chore/vps-migration-prep` is not a subset of `deploy/vps` and not a superset. It
forked at `e754cc7` and is missing three commits that `deploy/vps` has:

```
6d34f1c Merge branch 'feat/r2-storage' into deploy/vps
50c556e Compute the rate-limit increment in SQL, not in Python
735ef6d Add Cloudflare R2 as a media storage backend
```

**Merging PR #3 as it stands lands a `main` with no R2 storage backend and no
rate-limiter lost-update fix.** A later deploy from that `main` would revert both
in production. That is the ticket's own stated worst case — "a merge that reverts
production" — arriving through the front door rather than via a hotfix.

### 2. Production is demonstrably running `deploy/vps`, not `chore/vps-migration-prep`

Established without SSH, from the live service:

- The public feed `https://api.dsec.club/website/events` (200, 13 events) references
  89 media URLs, all under `https://media.dsec.club`.
- `HEAD https://media.dsec.club/event/10/0f7dc12a37b241c7a99fd5acb774b05d.jpg` →
  `200`, `server: cloudflare`, `cf-ray: …`,
  **`cache-control: public, max-age=31536000, immutable`**,
  `last-modified: Fri, 14 Aug 2026 09:32:47 GMT` (cutover day).
- That exact cache-control string is set **only** on the Cloudflare R2 upload path,
  `origin/deploy/vps:app/features/media/storage.py` → `CacheControl="public, max-age=31536000, immutable"`.
- The Supabase path — the other backend, and the only one on
  `chore/vps-migration-prep` — sets `"cache-control": "31536000"` (bare seconds, no
  `public`, no `immutable`) and serves from `*.supabase.co`, not a DSEC domain.
- `chore/vps-migration-prep` has **no `STORAGE_BACKEND` setting, no `R2_*` settings,
  and no `deploy/migrate-media-to-r2.py`** at all.

The fingerprint is exclusive. Production runs `deploy/vps` or a descendant.

> This is inference from observable behaviour, not proof of the deployed SHA.
> Only the box can give that, which is what the step-0b questions are for.
> I had queued four adversarial agents to try to refute this; all six workflow
> agents died on a session limit, so this rests on my own evidence above.

### 3. Merging `deploy/vps` does *not* fix "most of" the Vercel staleness

The ticket says it does. It does not. Still Vercel-framed **on `deploy/vps`**:

| File | Still stale after merging `deploy/vps` |
|---|---|
| `vercel.json` | **still present** — only added to `.dockerignore` |
| `README.md:21,58,120-143,196` | "Deploys to **Vercel**", a whole deploy section |
| `SECURITY.md` | ~25 lines; entire threat model framed on Vercel Firewall |
| `docs/configuration.md:4,33,44` | "set these as **Vercel project env vars**" |
| `docs/architecture.md:129` | "Runs as a single Vercel Function" |
| `.env.example:2,26,86` | "In production set these as Vercel project env vars" |
| `TODO.md:17,20,84,87,101` | Vercel project setup tasks |

`deploy/vps` *does* fix `app/config.py` (adds `APP_ENV`) and reword `app/core/net.py`.

Legitimate historical record — **keep**: `docs/deployment.md` (the ticket says so),
`docs/cutover.md`, `docs/vps-deployment.md`, `CHANGELOG.md`, the `compose.yaml` /
`Caddyfile` / `production-guard.yml` comments explaining what they replaced.

Out of scope here (**SEC-03**, do not touch): `app/auth.py:31-32` `_on_vercel()`,
and `tests/test_security_hardening.py`'s `VERCEL=1` cases.

`dsec-games/CROSS_REPOSITORY.md:8` — confirmed the only `CROSS_REPOSITORY.md` of the
six mentioning Vercel (`dsec-discord-bot` has no such file at all).

---

## Step 0a — divergence table

`git -C dsec-api fetch --all` ran clean.

| Branch | `main` ahead | branch ahead | contained in `deploy/vps`? |
|---|---|---|---|
| `deploy/vps` | 0 | 14 | — |
| `chore/vps-migration-prep` | 0 | 13 | **NO** (2 doc commits it lacks) |
| `fix/ratelimit-lost-updates` | 0 | 12 | **YES** — fully merged already |
| `feat/r2-storage` | 0 | 11 | **YES** — fully merged already |
| `ci/pytest` | 0 | 2 | NO |
| `fix/rest-scope-algebra` | 0 | 2 | NO |

`main` is behind all six and ahead of none.

`fix/ratelimit-lost-updates` and `feat/r2-storage` are already ancestors of
`deploy/vps` (`git merge-base --is-ancestor` → 0). They become empty at step 2 and
can be deleted then, no PR needed.

---

## Step 0d — Alembic: **NO NEW MIGRATIONS. `alembic upgrade head` IS A NO-OP.**

Walked the `down_revision` chain on all six branches:

| Branch | revisions | head | roots | dangling |
|---|---|---|---|---|
| `main` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `deploy/vps` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `chore/vps-migration-prep` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `fix/ratelimit-lost-updates` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `feat/r2-storage` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `ci/pytest` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |
| `fix/rest-scope-algebra` | 39 | `a2e6c4f8b1d3` | `7b59509e3c76` | none |

**Every branch: identical 39-revision linear chain, one head, no branch points.**

Revisions on one branch and not the other: **NONE.** So the "does it have a working
downgrade" question has no subject. (For the record, all 39 revisions on `main` have
a non-empty `downgrade()` body — zero are `pass`.)

The only Alembic *content* difference is one existing revision, edited by `c4b64a6`:

`alembic/versions/6b410fb3dad4_db_level_defaults_for_timestamps_and_.py` gains a
`_is_postgres()` guard that early-returns on SQLite. On Postgres it evaluates true
and the revision executes byte-identically. Its `revision` / `down_revision` are
unchanged, and it is already applied in production, so Alembic will not re-run it.
**Net effect on Neon: zero.**

### So: does merging `deploy/vps` require `alembic upgrade head` against Neon?

**NO — MERGING `deploy/vps` INTRODUCES NO MIGRATION AND REQUIRES NO SCHEMA CHANGE
AGAINST THE SHARED NEON DATABASE. THE CODE HEAD ALREADY EQUALS THE DEPLOYED HEAD.**

Two caveats, neither of which changes that answer:

1. `compose.yaml`'s `migrate` service runs `python scripts/migrate.py`
   (→ `alembic upgrade head`) on **every** `docker compose up`, and `api` waits on
   it. Given identical heads this is a no-op. And per the ticket we are **not
   deploying** after step 2, so it does not even run.
2. This proves the *code* agrees with itself. It does not prove the *database* is at
   `a2e6c4f8b1d3`. If Neon were behind, the next deploy would apply real migrations.
   Section C of `VPS-QUESTIONS.md` checks that with the read-only `scripts/check_neon.py`.

**No stop-for-human-decision is required on migration grounds.** The stop is on the
deployed-SHA and backup questions instead.

---

## Step 0c — is `deploy/backup-neon.sh` sufficient?

**As a script: yes. As an installed backup: not yet — the off-box copy is missing.**

The script is sound: `pg_dump --no-owner --no-acl` piped to `gzip -9`, refuses to
rotate if the dump looks empty, prunes past `RETAIN_DAYS`, rewrites the SQLAlchemy
URL to libpq form.

`docs/cutover.md` on `chore/vps-migration-prep` — a file that exists **on no other
branch** — records it as installed and verified on 2026-08-14: nightly 03:15 UTC,
14-day retention, first dump 1 MB, passed `gzip -t`, 58 tables / 22 `events` rows.

The gap, in the club's own words:

> **Still missing: the off-box copy.** `BACKUP_REMOTE` is unset, so the dumps only
> live on the VPS. That survives losing the *database* but not losing the *box*,
> which is half the point.

A restore procedure **is** written down (restore into a fresh Neon branch, never over
production) — but only in that file, which is on an unmerged branch. Losing the box
loses both the dumps and the runbook.

**My assessment:** adequate for this task's actual risk, which is near zero on the
schema (no migrations run). Not adequate as the club's standing backup. Fixing
`BACKUP_REMOTE` is worth doing but should not block OPS-02.

I still need you to confirm §D and §E of `VPS-QUESTIONS.md` rather than take the
2026-08-14 note on trust — it is thirteen days old and describes a cron job I cannot see.

---

## Full merge sequence dry-run — clean, and green at every gate

Throwaway clone, merged in the ticket's order, nothing pushed:

| Gate | Merged | Merge result | `pytest` |
|---|---|---|---|
| baseline | `main` as-is | — | **216 passed** |
| 1 | `+ ci/pytest` (PR #1) | clean | **216 passed** |
| 2a | `+ deploy/vps` | clean | **224 passed** |
| 2b | `+ chore/vps-migration-prep` (PR #3) | clean | **224 passed** |
| 3 | `+ fix/rest-scope-algebra` (PR #2) | clean | **233 passed** |

Zero conflicts anywhere. Baseline 216 matches the ticket exactly. Run with the
repo's own `.venv` (pytest 9.1.1); `tests/conftest.py` forces a throwaway SQLite
file and blanks the storage credentials, so nothing touched Neon or R2.

After all four merges every branch reports `N 0` — nothing left unmerged, satisfying
the acceptance criterion.

**And the useful part: after `deploy/vps` lands, PR #3's diff collapses to
`docs/cutover.md` alone (+76/-11).** So PR #3 does not need to be closed. It becomes
exactly what it should have been — the commit that records the completed cutover and
the verified backups.

---

## PR #2 cannot lock out an existing key — proven exhaustively

The ticket flags this as the one that "can lock out real callers". It is the reverse.

On `main`, `require_api_key` used a plain subset test. On the branch it uses
`has_scope`, where legacy `write` ⊇ every `read:*`/`write:*`/`read`, legacy `read` ⊇
every `read:*`, and `write:X` ⊇ `read:X`. Routes narrowed `read`→`read:{sponsors,
finance,games}` and `write`→`write:{…}` across 6 files, 30 call sites.

I enumerated every `require_api_key(...)` site on both branches and tested all
**1024** possible granted-scope sets against every route:

```
NO allow->deny regression found across every route x every scope set.
```

Any key that worked before still works. The change is strictly *more* permissive —
including one widening worth naming: a `write`-only key now also satisfies `read`,
which it did not before. That matches the MCP layer's long-standing behaviour and is
documented in the branch, but it is a real behaviour change, not a no-op.

**Blast radius in production is smaller still**, from live probing:

| Endpoint | live status | affected by PR #2? |
|---|---|---|
| `/website/events`, `/website/team`, `/website/linktree` | **200 unauthenticated** | **no** — public, no key involved |
| `/members/verify` | 401 (keyed) | **no** — still `require_api_key("read")` |
| `/games/*` | **404** — parked (`GAMES_ENABLED=false`) | not exercised in production |
| `/sponsors`, `/sponsor-packages`, `/sponsor-leads`, `/finance/*` | 401 (keyed) | **yes** — only `dsec-hub` calls these |

So the two things the ticket says to check after deploying PR #2 —
**dsec-website's public feed cannot break** (that route takes no key at all), and
**dsec-app's membership card cannot break** (`/members/verify` is untouched). The
only real consumer of the changed routes is `dsec-hub`.

I will still run the key inventory before deploying it. `GET /admin/keys` (basic
auth) returns `id, name, prefix, scopes, created_at, last_used_at, revoked` for every
key — confirmed live, 401 without credentials.

---

## Revised plan (unchanged in order; corrected in content)

0. **[BLOCKED — awaiting you]** `VPS-QUESTIONS.md` §A–§E.
1. PR #1 `ci/pytest` → `main`. Confirm `pytest` green on the push to `main`.
2. **New PR `deploy/vps` → `main`** — this is what production runs. Do not deploy.
   Verify `git merge-base --is-ancestor <SHA from §A1> origin/deploy/vps` first.
3. **PR #3 as-is** — by then a `docs/cutover.md`-only change. Do not deploy.
4. PR #2 `fix/rest-scope-algebra` → `main`, deployed on its own and watched.
5. Delete `fix/ratelimit-lost-updates` and `feat/r2-storage` (already contained).
6. Add `pytest` as a required check; `production-guard` only after step 2.
7. Vercel sweep — a real change set, not a side effect of step 2. Plus
   `dsec-games/CROSS_REPOSITORY.md:8`.

`curl -s https://api.dsec.club/health` after every merge-and-deploy step.

## What I need from you

1. §A–§D output from the box.
2. §E: has a Neon backup been taken today, and is the restore procedure recorded
   off-box?
3. A decision on finding 1: **open a new PR from `deploy/vps`**, which is what I
   recommend and which leaves PR #3 as a clean docs commit — or something else.
