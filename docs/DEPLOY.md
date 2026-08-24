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

Better than a terminal window, worse than a server. It survives a closed
terminal, a logout and a crash, and starts again at login. It does not run while
the Mac is asleep.

```bash
make service        # install and start it
make service-log    # watch it
make service-stop   # stop it, and stop it coming back
```

`make service` kills any copy you started by hand first, so the two cannot fight
over `getUpdates`. `KeepAlive` restarts the bot if it ever exits; a ten-second
`ThrottleInterval` means a broken `.env` produces one message every ten seconds in
the log rather than a spin.

### What sleep actually costs you

Nothing is lost. The outbox holds every notification with the moment it may go
out, and the first tick after waking delivers whatever came due. What you lose is
punctuality: a digest due at 08:00 on a sleeping laptop arrives when you open the
lid.

Check what your Mac does:

```bash
pmset -g | grep -E "^ *(sleep|powernap|tcpkeepalive)"
```

`sleep 1` means it sleeps after a minute of idle on this power source. To keep it
awake while plugged in, either run `caffeinate -s` in a spare terminal, or set it
permanently (needs your password):

```bash
sudo pmset -c sleep 0
```

`-c` is "on charger only", so the battery still sleeps normally. This is the whole
difference between a tracker that taps you on the shoulder and one that tells you
this morning's news at lunchtime — and it is why a €4 VPS or a Raspberry Pi is
the real answer.

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
