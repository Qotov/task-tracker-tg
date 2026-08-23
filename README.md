# task-tracker-tg

A shared Telegram task bot for two people — a household, a project, anyone who
shares a list of dated obligations. Two whitelisted Telegram accounts, one private
group, one SQLite file, no web interface and nothing to host beyond one small
process. See [docs/TASK.md](docs/TASK.md) for the
full specification, [CLAUDE.md](CLAUDE.md) for the working rules, and
[DECISIONS.md](DECISIONS.md) for every choice the spec left open.

**Status: phases 1–4, 6 and 7 are built.** Templates (phase 5 — `/templates`,
`/new <template> <date>` and the three YAML files) are the one thing not here.

## Setup from zero

1. **Install [uv](https://docs.astral.sh/uv/)**, then `make sync` to create the
   virtual environment with Python 3.12.
2. **Create the bot.** Talk to [@BotFather](https://t.me/BotFather): `/newbot`,
   pick a name and a username, and copy the token it gives you.
3. **Get both Telegram user ids** — each of you messages
   [@userinfobot](https://t.me/userinfobot), which replies with a number.
4. **Write `.env`.** `cp .env.example .env`, then fill in `BOT_TOKEN` and both
   ids in `ALLOWED_USER_IDS`, comma-separated. `.env` is gitignored and must
   never be committed. Nobody outside that list gets any reply at all.
5. **Set your Telegram usernames first.** Your handle in the bot (`@yourname`) is
   taken from your Telegram username the first time you say `/start`, and never
   changes after that.
6. **`make run`.** The database is created on first start with mode 0600, and
   the migrations are applied automatically.
7. **Both of you send `/start`** in a private chat with the bot. That registers
   you and is how the bot learns where to send reminders.
8. **Create the group**, add both people and the bot, then in BotFather use
   `/mybots → Bot Settings → Group Privacy → Turn off` so the bot can read plain
   group messages. Without this, only commands reach it. The first group that
   speaks to the bot is the one it works in; it stays silent in any other.

Run exactly one copy of the bot. Two copies polling the same token gives
`Conflict: terminated by other getUpdates request` and neither works properly.

## Using it

Any plain message becomes a task:

```
@partner call the landlord about the notice tomorrow #move
```

`@name` or `@me` sets the single owner, `#project` sets the project, and a date —
`today`, `tomorrow`, `mon`…`sun`, `20/09`, `20.09`, `24 Sep`, `2026-09-20`,
`+3d`, `+2w`, `+1m`, any of them with a time like `14:30` — sets when. A date
without a time means 09:00 in your `TZ`. There are no priorities: the due date carries
the urgency.

Everything else is buttons. A todo card carries **Done**, **+1 day**,
**Waiting**, **→ partner**, **Subtask**, **Both**, **Note**, **Reschedule** and
**Drop**; a waiting card swaps in **+7 days** and **To do**; a closed card keeps
**Reopen**. **Reschedule** offers Today / Tomorrow / +3 days / Next week /
+1 month / +3 months / No date on the same card.

In a private chat a keyboard sits under the text field with **Today**, **Week**,
**Month**, **Overdue**, **Mine**, **Board**, **New task** and **Menu**. Every
list puts an `#id` button under it, so a task can be opened and acted on without
typing its number.

### What it does on its own

- **Reminds** the owner when a task is due, and once a day while it stays late —
  for three days, after which it only appears in the digest.
- **A morning digest** at each person's chosen hour: due today, overdue, what
  came free, and anything waiting on somebody.
- **Never inside quiet hours.** A message that would land in your quiet window is
  held and delivered when it ends. Nothing overrides this.
- **Announces in the group** when finishing one task frees another.
- **Keeps a pinned dashboard** in the group, edited in place at most once every
  five seconds.

### For the two of you

**👥 Both** puts a copy of an errand on the other list — a task has exactly one
owner, so two signatures are two tasks. A card says *asked by …* when the other
one wrote it for you. Adding something that already exists gets a quiet
nudge naming it. A due date landing on a public holiday is flagged, because the offices will be
shut — set `HOLIDAYS=off` in `.env` if that is not useful where you are (only
`FR` ships a table today).

### Documents

Send a scan or a photo to the bot and it offers the five newest open tasks, plus
**Search** and **Keep without a task**. Only Telegram's `file_id` is stored —
nothing is ever written to disk. `/docs lease` searches titles, projects, file
names and captions and sends the matches back, each captioned with its task.

### Commands

`/menu` `/new` `/board` `/add` `/sub` `/done` `/drop` `/due` `/own` `/note`
`/wait` `/block` `/repeat` `/today` `/week` `/month` `/overdue` `/mine` `/docs`
`/export` `/settings` `/dash` `/health` `/help` `/start`

`/repeat 12 weekly:mon` also takes `daily`, `monthly:15`, `yearly:09-20` and
`off`. When a repeating task is closed the next one appears with its dates
shifted; its subtasks come with it, its notes do not.

## Running it on a server

See [docs/DEPLOY.md](docs/DEPLOY.md) for the full walkthrough, including the
macOS stopgap and how to move the database.

```bash
sudo cp deploy/bot.service /etc/systemd/system/task-tracker-tg.service
sudo systemctl daemon-reload && sudo systemctl enable --now task-tracker-tg
```

The unit runs `uv run python -m bot.main` as a dedicated non-root user with
`Restart=always`. Adjust `WorkingDirectory`, `User` and `EnvironmentFile` to match
where you put it.

### Backups

`scripts/backup.sh` takes a consistent snapshot with `sqlite3 .backup` (safe
while the bot is running), gzips it, uploads it to `BACKUP_CHAT_ID` when that is
set, and keeps the last seven locally. A nightly cron entry:

```
15 3 * * * BOT_TOKEN=… DB_PATH=/srv/task-tracker-tg/tasks.db BACKUP_CHAT_ID=… /srv/task-tracker-tg/scripts/backup.sh
```

## Development

```bash
make test
```

Runs `ruff check`, then `mypy`, then `pytest` — 268 tests, no network. `make lint`
checks formatting and `make fmt` applies it.
