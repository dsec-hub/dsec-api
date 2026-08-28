# OPS-02 — Step 0 gate: commands to run ON THE OVH BOX

Run these **before any merge happens**. Paste the whole output back.
Nothing here writes to the database or changes the running service.
Everything is read-only except writing one patch file into your home directory.

Box: `<VPS_IP>` (`<VPS_HOSTNAME>`). Deploy dir: `~/dsec-api`.

> SSH will fail without the `~/.ssh/config` block in `docs/cutover.md` — the deploy
> key has a non-default filename. If you get `Permission denied (publickey)`, stop:
> anything you paste after that runs **on your laptop**, not the box.

---

## A. What commit is actually deployed, and is there a hotfix on it?

```bash
ssh dsec-vps
cd ~/dsec-api

echo "=== A1 exact deployed commit ==="
git rev-parse HEAD

echo "=== A2 branch / detached / ahead-behind ==="
git status --porcelain=v1 -b | head -1
git branch -vv
git log --oneline -5

echo "=== A3 does that commit exist on the remote at all? ==="
git fetch --all --quiet
for b in main deploy/vps chore/vps-migration-prep feat/r2-storage fix/ratelimit-lost-updates; do
  git merge-base --is-ancestor HEAD origin/$b 2>/dev/null \
    && echo "  HEAD IS contained in origin/$b" \
    || echo "  HEAD is NOT contained in origin/$b"
done

echo "=== A4 uncommitted tracked changes (THE HOTFIX RISK) ==="
git status --porcelain
git diff --stat
git diff --cached --stat

echo "=== A5 untracked files — git diff does NOT show these ==="
git ls-files --others --exclude-standard

echo "=== A6 stashes ==="
git stash list
```

**If A4 or A5 shows anything, capture it before doing anything else:**

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
git diff > ~/vps-hotfix-tracked-$STAMP.patch
git diff --cached > ~/vps-hotfix-staged-$STAMP.patch
tar czf ~/vps-hotfix-untracked-$STAMP.tgz $(git ls-files --others --exclude-standard) 2>/dev/null || true
ls -la ~/vps-hotfix-*
```

Then copy them off the box (run this **on your laptop**, not the box):

```bash
scp 'dsec-vps:~/vps-hotfix-*' ./
```

Do **not** `git checkout`, `git stash`, `git pull`, or `git reset` on the box.
Capturing first is the whole point of this step.

---

## B. Which storage/config is the box actually running?

`.env.production.example` on `deploy/vps` does **not** list `STORAGE_BACKEND` or any
`R2_*` variable, yet production is demonstrably serving media from Cloudflare R2.
That means the live `.env` carries settings that exist in **no committed example
file**. This is a second, quieter version of the same "config exists only on the
box" risk.

**Names only — this deliberately redacts every value. Do not paste raw `.env`.**

```bash
cd ~/dsec-api
sed -E 's/=.*/=<redacted>/' .env | sort
```

---

## C. What Alembic revision is the database actually at?

Branch-to-branch there are **no new migrations** (see the report), but that only
proves the *code* agrees. This proves the *database* agrees.

```bash
cd ~/dsec-api
docker compose run --rm --no-deps migrate python scripts/check_neon.py
```

Expected tail: `Alembic revision: a2e6c4f8b1d3`.
`check_neon.py` is read-only — it inspects the schema and never creates or drops.

**If it prints anything other than `a2e6c4f8b1d3`, stop and tell me.** It would mean
the next `docker compose up` runs real migrations against the shared Neon database.

---

## D. Backups — are they real, and is there an off-box copy?

```bash
ssh dsec-vps-root      # the `ubuntu` user; `dsec` has no working sudo

echo "=== D1 dumps present and recent ==="
sudo ls -la /var/backups/dsec/ | tail -20

echo "=== D2 last run ==="
sudo tail -30 /var/log/dsec-backup.log

echo "=== D3 cron is installed ==="
sudo crontab -l | grep -i dsec

echo "=== D4 is the OFF-BOX copy configured? (names only) ==="
sudo sed -E 's/=.*/=<redacted>/' /etc/dsec-backup.env

echo "=== D5 newest dump is not truncated ==="
sudo sh -c 'f=$(ls -t /var/backups/dsec/dsec-*.sql.gz | head -1); echo "$f"; gzip -t "$f" && echo "gzip OK"; gzip -dc "$f" | wc -c'
```

---

## E. Two questions I need answered in words

1. **Has a Neon backup been taken specifically for this change, today, and is the
   restore procedure written down somewhere other than the box?**
2. **Do you want me to proceed given the finding in the report** that PR #3 is not
   the branch production is running?
