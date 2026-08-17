#!/usr/bin/env bash
# Nightly backup of the task database (section 16).
#
# Uses sqlite3's own .backup, which is safe while the bot is running — copying
# the file with cp is not. Uploads to Telegram when BACKUP_CHAT_ID is set, keeps
# the last seven locally, and never leaves the plain copy behind.
#
# Cron, every night at 03:15:
#   15 3 * * * BOT_TOKEN=... DB_PATH=/srv/task-tracker-tg/tasks.db \
#              BACKUP_CHAT_ID=... /srv/task-tracker-tg/scripts/backup.sh

set -euo pipefail

DB_PATH="${DB_PATH:-tasks.db}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$DB_PATH")/backups}"
KEEP="${KEEP:-7}"

if [ ! -f "$DB_PATH" ]; then
  echo "backup: no database at $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
stamp="$(date +%F)"
plain="/tmp/tasks-${stamp}.db"
archive="${BACKUP_DIR}/tasks-${stamp}.db.gz"

# A consistent snapshot, even mid-write.
sqlite3 "$DB_PATH" ".backup '${plain}'"
gzip -c "$plain" > "$archive"
rm -f "$plain"
chmod 600 "$archive"

if [ -n "${BACKUP_CHAT_ID:-}" ] && [ -n "${BOT_TOKEN:-}" ]; then
  curl -sS -o /dev/null \
    -F "chat_id=${BACKUP_CHAT_ID}" \
    -F "document=@${archive}" \
    -F "caption=task-tracker backup ${stamp}" \
    "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
    || echo "backup: upload failed, the local copy is still at ${archive}" >&2
fi

# Keep the last N archives and nothing older.
ls -1t "${BACKUP_DIR}"/tasks-*.db.gz 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
done

echo "backup: ${archive}"
