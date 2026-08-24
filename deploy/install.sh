#!/usr/bin/env bash
# One-shot installer for a fresh Debian or Ubuntu box (a €4 VPS, a Raspberry Pi).
#
# Run it as root on the server:
#   curl -fsSL https://raw.githubusercontent.com/<you>/task-tracker-tg/main/deploy/install.sh | bash
# or, having cloned it already:
#   sudo bash deploy/install.sh
#
# It is idempotent: run it again after a git pull to update.

set -euo pipefail

REPO="${REPO:-https://github.com/Qotov/task-tracker-tg.git}"
HOME_DIR="${HOME_DIR:-/srv/task-tracker-tg}"
SERVICE_USER="${SERVICE_USER:-taskbot}"

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

[ "$(id -u)" -eq 0 ] || { echo "run this as root (sudo bash deploy/install.sh)"; exit 1; }

say "packages"
apt-get update -qq
apt-get install -y -qq git sqlite3 curl ca-certificates

say "user and directory"
id "$SERVICE_USER" >/dev/null 2>&1 || adduser --system --group --home "$HOME_DIR" "$SERVICE_USER"
mkdir -p "$HOME_DIR"

say "uv"
if ! [ -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

say "code"
if [ -d "$HOME_DIR/.git" ]; then
  sudo -u "$SERVICE_USER" git -C "$HOME_DIR" pull --ff-only
else
  # The directory exists (adduser made it), so clone into it rather than over it.
  sudo -u "$SERVICE_USER" git clone "$REPO" "$HOME_DIR/checkout"
  sudo -u "$SERVICE_USER" cp -r "$HOME_DIR/checkout/." "$HOME_DIR/"
  rm -rf "$HOME_DIR/checkout"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$HOME_DIR"

say "dependencies"
cd "$HOME_DIR"
sudo -u "$SERVICE_USER" /usr/local/bin/uv sync --frozen

say "configuration"
if [ ! -f "$HOME_DIR/.env" ]; then
  sudo -u "$SERVICE_USER" cp "$HOME_DIR/.env.example" "$HOME_DIR/.env"
  chmod 600 "$HOME_DIR/.env"
  cat <<'MSG'

  .env has been created but is EMPTY. Fill it in now:

      nano /srv/task-tracker-tg/.env

  You need BOT_TOKEN and ALLOWED_USER_IDS. Then run this script again,
  or just: systemctl restart task-tracker-tg

MSG
fi
chmod 600 "$HOME_DIR/.env"

say "service"
sed -e "s|/srv/task-tracker-tg|$HOME_DIR|g" -e "s|User=taskbot|User=$SERVICE_USER|" \
    -e "s|Group=taskbot|Group=$SERVICE_USER|" \
    "$HOME_DIR/deploy/bot.service" > /etc/systemd/system/task-tracker-tg.service
systemctl daemon-reload
systemctl enable task-tracker-tg

say "nightly backup"
BACKUP_LINE="15 3 * * * cd $HOME_DIR && DB_PATH=$HOME_DIR/tasks.db bash $HOME_DIR/scripts/backup.sh"
( crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -v backup.sh ; echo "$BACKUP_LINE" ) \
  | crontab -u "$SERVICE_USER" -

if grep -q '^BOT_TOKEN=.\+' "$HOME_DIR/.env" 2>/dev/null; then
  systemctl restart task-tracker-tg
  sleep 3
  systemctl --no-pager --lines=10 status task-tracker-tg || true
  say "done — follow it with: journalctl -u task-tracker-tg -f"
else
  say "fill in .env, then: systemctl start task-tracker-tg"
fi
