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

`Dockerfile` and `fly.toml` are in the repository root. The database lives on a
mounted volume at `/data`, never in the image, so a redeploy cannot take your
tasks with it. Run every command from the repository root.

**Step 0, and it is not optional: stop any other copy first.** Two processes
polling one bot token give `Conflict: terminated by other getUpdates request`,
and updates are split unpredictably between them — a task typed in the group
lands in whichever database won that round.

```bash
make service-stop
launchctl list | grep task-tracker      # must print nothing
pgrep -f "python -m bot.main"           # must print nothing
```

Then bring it up:

```bash
fly launch --no-deploy --name task-tracker-tg
fly volumes create data --size 1 --region cdg
fly secrets set BOT_TOKEN=... ALLOWED_USER_IDS=... GEMINI_API_KEY=...
fly deploy
```

Give the volume the **same region as `primary_region`**, or the machine cannot
attach it. `TZ` stays in `[env]` — it is not a secret. Do not set `DB_PATH`: the
Dockerfile's `ENV DB_PATH=/data/tasks.db` is what puts the file on the volume.
Leave `GEMINI_API_KEY` out and the second-chance parser silently switches off,
with nothing in the logs to say why.

### Moving an existing database across

The first deploy comes up on an **empty** volume and looks perfectly healthy
while being completely blank, so this step is easy to skip by accident. It has to
come *after* the first deploy, because `fly ssh` needs a machine that is running.

```bash
sqlite3 tasks.db ".backup '/tmp/tasks.db'"   # online snapshot; never plain cp
fly ssh sftp shell                            # then, inside the session:
#   put /tmp/tasks.db /data/tasks.db.incoming
```

Upload *beside* the live file, verify it, and only then swap — so a truncated
transfer cannot destroy what is already there:

```bash
fly ssh console -C "/bin/sh -c 'sqlite3 /data/tasks.db.incoming \"PRAGMA integrity_check; select count(*) from tasks;\"'"
fly ssh console -C "/bin/sh -c 'mv /data/tasks.db.incoming /data/tasks.db'"
fly apps restart task-tracker-tg
```

Take the snapshot only once the local bot has stopped, or any task created
between the snapshot and the cutover is lost without a trace.

### Two traps on any container host

Give it a **persistent volume** — an ephemeral filesystem loses every task on
redeploy. And make sure it runs as a **worker, not a web service**: this bot
listens on no port, so a platform that scales to zero waiting for an HTTP request
will simply stop it. That is why `fly.toml` has no `[http_service]` block.

### What does not come across

The nightly backup in Option A is a host cron entry, and there is no cron in the
container — so on fly, **nothing backs the volume up**. A fly volume is a single
unreplicated disk on one host. Until an in-process backup job exists, take one by
hand now and then:

```bash
fly ssh console -C "/bin/sh -c 'sqlite3 /data/tasks.db \".backup /data/snap.db\"'"
fly ssh sftp get /data/snap.db ./tasks-from-fly.db
```

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
