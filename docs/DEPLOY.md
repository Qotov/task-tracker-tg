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

## Choosing where

| | cost | always on | effort |
| --- | --- | --- | --- |
| **This Mac** (`make service`) | free | only while awake | one command, already done |
| **VPS** — Hetzner, DigitalOcean, Scaleway | €4/mo | yes | one script |
| **Raspberry Pi** at home | free after the Pi | yes | one script |
| **fly.io / Railway** (container) | ~$2–4/mo | yes | needs their CLI |

Anything that stays awake beats the laptop. The bot uses a few MB of RAM and no
inbound ports, so the smallest box any provider sells is oversized for it.

## Option A — a small server (recommended)

A €4/month VPS or a Raspberry Pi. One script does all of it:

```bash
sudo bash deploy/install.sh
```

It installs git, sqlite3 and uv; creates a `taskbot` system user; clones or
updates the code; runs `uv sync --frozen`; writes and enables the systemd unit;
and installs the nightly backup cron. Run it again after a `git pull` to update —
it is idempotent.

The first run stops and tells you to fill in `.env`:

```bash
nano /srv/task-tracker-tg/.env      # BOT_TOKEN and ALLOWED_USER_IDS
systemctl start task-tracker-tg
journalctl -u task-tracker-tg -f
```

Set `BACKUP_CHAT_ID` to your own Telegram id as well, and the gzipped database
arrives in your chat every night — off-site backup with no extra service.

## Option C — a container (fly.io, Railway, a NAS)

`Dockerfile` and `deploy/fly.toml` are in the repository. The database lives on a
mounted volume at `/data`, never in the image, so a redeploy cannot take your
tasks with it.

```bash
fly launch --no-deploy --copy-config --config deploy/fly.toml
fly volumes create data --size 1
fly secrets set BOT_TOKEN=... ALLOWED_USER_IDS=... TZ=Europe/Paris
fly deploy
```

Two things to watch on any container host: give it a **persistent volume** (an
ephemeral filesystem loses every task on redeploy), and make sure it is a
**worker, not a web service** — this bot listens on no port, and a platform that
scales to zero waiting for an HTTP request will stop it.

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
