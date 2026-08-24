<div align="center">

# task-tracker-tg

**A shared task tracker for two people, living entirely inside Telegram.**

[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![SQLite](https://img.shields.io/badge/storage-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Tests](https://img.shields.io/badge/tests-343%20passing-3fb950)](#development)
[![Lint](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=D7FF64)](https://docs.astral.sh/ruff/)
[![Types](https://img.shields.io/badge/types-mypy-1F5082)](https://mypy-lang.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

---

Two people, one private group, and a stack of things that both of them need to
happen on time — a move, a pile of official paperwork, a household. This is a bot
for exactly that, and deliberately nothing more.

A plain message in the group becomes a task. Everything after that is buttons.
There is no web interface, no account to create, no app to install: the whole
thing is one Python process, one SQLite file, and long polling. It runs happily
on a Raspberry Pi.

**Design constraints, on purpose:**

- **No priority field.** The due date carries the urgency. `!urgent` is stripped
  and answered with a note that priorities are not supported.
- **Exactly one owner per task.** Two signatures are two tasks — there is a
  button for that.
- **No notification ever lands inside quiet hours.** Anything that would is held
  and delivered when the window closes. Nothing overrides it, not escalation, not
  an overdue chase, not a digest.

## Creating tasks

Any message that is not a command becomes a task:

```
@partner call the landlord about the notice tomorrow #move
```

| Syntax | Meaning |
| --- | --- |
| `@name`, `@me` | the single owner |
| `#project` | groups related tasks |
| `today`, `tomorrow`, `mon`…`sun` | the day |
| `20/09`, `20.09`, `24 Sep`, `2026-09-20` | an explicit date |
| `+3d`, `+2w`, `+1m` | relative to now |
| `14:30` | a time; without one, a date means 09:00 |

## Reading them

Every line follows one system: a glyph in the left column for urgency, the task
in bold, and everything *about* it in italics afterwards — so a list can be
scanned rather than read.

```
📅 Today · Tue 15 Sep

Late
🔴 pay the deposit to the agency — 2 days late · robin · move · #1

Still to come
🟠 book the movers — today at 17:30 · robin · move · #2

Blocked
🔒 book the mairie appointment — blocked by #3 · sam · #4
```

🔴 late · 🟠 today · ⚪️ later · 🔒 blocked · ⏳ waiting · ✅ done

Dates are phrased the way you would say them (*2 days late*, *in 3 hours*,
*Friday*), and a time appears only when you chose one.

A task card carries **Done**, **+1 day**, **Waiting**, **→ partner**,
**Subtask**, **Both**, **Note**, **Reschedule** and **Drop**. Waiting cards swap
in **+7 days** and **To do**; closed cards keep **Reopen**. In a private chat a
keyboard sits under the text field with **Today**, **Week**, **Month**,
**Overdue**, **Mine**, **Board** and **New task**. Every list puts an `#id`
button underneath, so a task can be opened without typing its number, and pages
ten at a time once there are more.

## What it does on its own

- **Reminds** the owner when a task falls due, then once a day while it stays
  late — for three days, after which the digest carries it.
- **A morning digest** at each person's chosen hour: due today, overdue, what
  came free, and anything waiting on someone.
- **Announces in the group** when finishing one task unblocks another.
- **Keeps a pinned dashboard** in the group, edited in place at most once every
  five seconds.
- **Holds anything due inside quiet hours** until the window closes.

`/stats` reports the fortnight behind you — what you closed against what you
added, a streak, how much of it made its date, and the split between the two of
you, each with a sentence saying what the number means. It reads from an
append-only event log, so reopening a task cannot rewrite last week.

## Documents

Send a scan or a photo and the bot offers the five newest open tasks, plus
**Search** and **Keep without a task**. Only Telegram's `file_id` is stored —
nothing is written to disk. `/docs lease` searches titles, projects, file names
and captions, and sends the matches back captioned with their task.

## Optional AI date parsing

The rule-based parser handles everything it recognises, offline, and always runs
first. Set `GEMINI_API_KEY` and a message it found **no date** in, longer than
eight words, gets a second reading that fills in the date, the project and any
obvious subtasks:

> *we really need to sort out the deposit before the inspection at the end of
> next month* → a task due 30 September with two steps under it.

The task is saved and its card is on screen **before** the model is asked, so a
slow or failed answer costs nothing. It never rewrites your title, never
overrides a date the rules already found, and is abandoned after four seconds.
With no key set it never runs and nothing leaves your machine — which is the
default, and worth a deliberate choice either way, since the document vault holds
identifiers.

## Setup

**Requirements:** Python 3.12 and [uv](https://docs.astral.sh/uv/).

1. **Create the bot.** Message [@BotFather](https://t.me/BotFather): `/newbot`,
   pick a name and a username, copy the token.
2. **Get both Telegram user ids** — each person messages
   [@userinfobot](https://t.me/userinfobot), which replies with a number.
3. **Set your Telegram usernames** before first use. Your handle in the bot
   (`@yourname`) is taken from it at first `/start` and never changes after.
4. **Configure and run:**

   ```bash
   uv sync
   cp .env.example .env      # then fill it in
   make run
   ```

   The database is created on first start with mode 0600 and migrations are
   applied automatically.
5. **Both people send `/start`** in a private chat with the bot. That registers
   them and is how the bot learns where to send reminders.
6. **Create the group**, add both people and the bot, then in BotFather use
   `/mybots → Bot Settings → Group Privacy → Turn off` so plain group messages
   reach it. The first group that talks to the bot is the one it works in; it
   stays silent in any other.

> Run exactly one copy. Two processes polling the same token produce
> `Conflict: terminated by other getUpdates request` and neither works properly.

### Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_TOKEN` | ✅ | — | from BotFather |
| `ALLOWED_USER_IDS` | ✅ | — | comma-separated; nobody else gets any reply |
| `DB_PATH` | | `./tasks.db` | where the SQLite file lives |
| `TZ` | | `Europe/Paris` | display timezone; storage is always UTC |
| `GEMINI_API_KEY` | | — | enables the second-chance parser |
| `GEMINI_MODEL` | | `gemini-3.5-flash-lite` | model for that parser |
| `BACKUP_CHAT_ID` | | — | chat the nightly backup is uploaded to |
| `HOLIDAYS` | | `FR` | flag due dates on public holidays, or `off` |

Startup fails loudly and specifically when a required variable is missing.

### Commands

```
/menu   /new    /board  /add    /sub    /done   /drop   /due    /own
/note   /wait   /block  /repeat /today  /week   /month  /overdue
/mine   /docs   /export /find   /stats  /health /settings /dash
/group  /help   /start
```

`/repeat 12 weekly:mon` also accepts `daily`, `monthly:15`, `yearly:09-20` and
`off`. Closing a repeating task creates the next one with its dates shifted;
subtasks come along, notes do not.

## Deployment

On macOS, hand it to `launchd` — it then survives a closed terminal, a logout and
a crash, and restarts at login:

```bash
make service        # make service-log follows it, make service-stop removes it
```

It does not run while the machine is asleep. Nothing is lost, but a digest due at
08:00 arrives when the lid opens. `sudo pmset -c sleep 0` avoids that on mains
power.

For reminders that always land on time, run it somewhere that stays awake. On a
fresh Debian or Ubuntu host one script installs a `taskbot` user, `uv`, a systemd
unit with `Restart=always`, and a nightly backup:

```bash
sudo bash deploy/install.sh
```

A `Dockerfile` and a `fly.toml` are included for container hosts. Two traps apply
to any of them: the database needs a **persistent volume**, or a redeploy erases
every task; and it must run as a **worker, not a web service**, because it listens
on no port and a platform that scales to zero will simply stop it.

See [docs/DEPLOY.md](docs/DEPLOY.md) for the full walkthrough, including moving an
existing database across without corrupting it.

### Backups

`scripts/backup.sh` takes a consistent snapshot with `sqlite3 .backup` — safe
while the bot is running — gzips it, uploads it to `BACKUP_CHAT_ID` if set, and
keeps the last seven locally.

```
15 3 * * * DB_PATH=/srv/task-tracker-tg/tasks.db /srv/task-tracker-tg/scripts/backup.sh
```

## Development

```bash
make test     # ruff check, then mypy, then pytest — 343 tests, no network
make lint     # formatting check
make fmt      # apply it
```

Handlers stay thin: a handler parses arguments, calls one function in
`bot/services/`, and hands the result to `bot/render.py`. All logic lives in
`bot/services/` so the tests run without Telegram. Schema changes are numbered SQL
files in `bot/migrations/`, applied on startup against a `schema_version` table.
Timestamps are stored as UTC ISO 8601 and rendered in the configured zone.

`scripts/memprobe.py` measures resident memory under load, for choosing a
container size from evidence rather than guesswork.

## Not implemented

Templates — expanding a named checklist into a dated tree of tasks — are
specified but not built.

## License

[MIT](LICENSE)
