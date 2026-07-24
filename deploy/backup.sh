#!/usr/bin/env bash
# Weekly SQLite backup (§8.2). Uses the online-backup API so it's safe while
# the bot is running. Schedule via cron, e.g. Sundays 02:00:
#   0 2 * * 0  /home/ubuntu/nyaham-bot/deploy/backup.sh >> /home/ubuntu/nyaham-bot/logs/backup.log 2>&1
set -euo pipefail

# Repo root = parent of this script's dir (works regardless of cwd).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${DB_PATH:-$ROOT/data/cache.sqlite}"
DEST="$ROOT/backups"
KEEP="${BACKUP_KEEP:-8}"   # retain the last N backups

mkdir -p "$DEST"
if [[ ! -f "$DB" ]]; then
  echo "$(date -u +%FT%TZ) no DB at $DB — nothing to back up"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/cache-$STAMP.sqlite"

# Prefer a consistent online backup; fall back to a plain copy.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$OUT'"
else
  cp "$DB" "$OUT"
fi
gzip -f "$OUT"
echo "$(date -u +%FT%TZ) backed up -> ${OUT}.gz"

# Prune old backups, keeping the newest $KEEP.
ls -1t "$DEST"/cache-*.sqlite.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
