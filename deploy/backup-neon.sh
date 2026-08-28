#!/usr/bin/env bash
#
# Nightly logical backup of the Neon database, kept OFF the VPS.
#
# Neon is the club's single source of truth: dsec-hub writes it directly via
# Drizzle and this API owns its schema. Neon's own branching/PITR covers most
# failure modes, but it does NOT cover "someone ran a bad UPDATE and nobody
# noticed for a fortnight", and it disappears entirely if the Neon account
# lapses. This is the independent copy.
#
# Install on the VPS:
#   sudo cp deploy/backup-neon.sh /usr/local/bin/dsec-backup
#   sudo chmod +x /usr/local/bin/dsec-backup
#   sudo crontab -e
#     15 3 * * *  DATABASE_URL='postgresql://...' /usr/local/bin/dsec-backup
#
# Set BACKUP_REMOTE to an rclone remote (e.g. "gdrive:dsec-backups") to copy the
# dump off the box. Without it the backup only survives loss of the database,
# not loss of the VPS — which is half the point.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dsec}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
DATABASE_URL="${DATABASE_URL:-}"

[ -n "$DATABASE_URL" ] || { echo "DATABASE_URL is not set" >&2; exit 1; }

# The app stores DATABASE_URL in SQLAlchemy form; pg_dump wants plain libpq.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/dsec-${STAMP}.sql.gz"

echo "==> dumping to $OUT"
# --no-owner / --no-acl so the dump restores cleanly into a fresh Neon branch or
# a local Postgres without needing the original role names to exist.
pg_dump --no-owner --no-acl --format=plain "$PG_URL" | gzip -9 > "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "==> wrote $SIZE"

# A dump that restores to nothing is worse than no dump, because it is believed.
if [ "$(gzip -dc "$OUT" | head -c 1000 | wc -c)" -lt 100 ]; then
    echo "ERROR: dump looks empty — not rotating old backups" >&2
    exit 1
fi

if [ -n "$BACKUP_REMOTE" ]; then
    echo "==> copying to $BACKUP_REMOTE"
    rclone copy "$OUT" "$BACKUP_REMOTE" || echo "WARNING: offsite copy failed" >&2
fi

echo "==> pruning local backups older than ${RETAIN_DAYS}d"
find "$BACKUP_DIR" -name 'dsec-*.sql.gz' -mtime "+$RETAIN_DAYS" -delete

echo "==> done"
