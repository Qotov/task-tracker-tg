# Deploying

The bot is one long-polling process and one SQLite file. It needs no domain, no
TLS certificate, no inbound port, and no database server. Anything that stays
switched on will do.

**Run exactly one copy.** Two processes polling the same token fight over
`getUpdates` and neither works. That includes a copy left running on your laptop
after you move to a server.

## Why it matters where you run it

Reminders, the digest and the follow-up pings are produced by a 60-second tick
inside the process. Nothing is lost while the bot is down — the outbox holds
messages and delivers them on the next start — but they arrive **late**. A bot
that lives on a laptop that sleeps at night will deliver the morning digest at
lunchtime.

## Option A — a small server (recommended)

Any €4/month VPS, or a Raspberry Pi on your own network.

```bash
# on the server, as root
adduser --system --group --home /srv/task-tracker-tg taskbot
apt install -y git sqlite3 curl
curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --install-dir /usr/local/bin

sudo -u taskbot git clone <your repo url> /srv/task-tracker-tg
cd /srv/task-tracker-tg
sudo -u taskbot cp .env.example .env    # then fill in BOT_TOKEN and ALLOWED_USER_IDS
chmod 600 .env
sudo -u taskbot uv sync

cp deploy/bot.service /etc/systemd/system/task-tracker-tg.service
systemctl daemon-reload
systemctl enable --now task-tracker-tg
systemctl status task-tracker-tg
journalctl -u task-tracker-tg -f
```

The unit restarts on crash and starts at boot. Adjust `User`, `WorkingDirectory`
and `EnvironmentFile` if you put it somewhere else.

Nightly backup, as the same user:

```
15 3 * * * BOT_TOKEN=… DB_PATH=/srv/task-tracker-tg/tasks.db BACKUP_CHAT_ID=… /srv/task-tracker-tg/scripts/backup.sh
```

Set `BACKUP_CHAT_ID` to your own Telegram user id and the gzipped database is
sent to you every night — off-site backup with no extra service.

## Option B — this Mac, in the background

Better than a terminal window, worse than a server: it survives a closed
terminal, a crash and a logout, but not sleep.

```bash
cp deploy/com.task-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.task-tracker.plist
tail -f /tmp/task-tracker.log
```

To stop it: `launchctl unload ~/Library/LaunchAgents/com.task-tracker.plist`.

To keep the Mac awake while it runs: `caffeinate -s` in a spare terminal, or
System Settings → Lock Screen → *Prevent automatic sleeping when the display is
off* while on power.

## Moving the data across

The database is one file. Stop the bot on both ends, copy it, start the new one:

```bash
sqlite3 tasks.db ".backup /tmp/tasks.db"     # safe even while running
scp /tmp/tasks.db taskbot@server:/srv/task-tracker-tg/tasks.db
```

The group id, the pinned dashboard id, both people and every task come with it.

## After any deploy

Send `/health` to the bot. It answers in one message: whether the scheduler is
ticking, how many notifications are held, whether it can reach both of you,
whether the group and the dashboard are linked.
