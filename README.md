# task-tracker-tg

A shared Telegram task bot for two people. See [docs/TASK.md](docs/TASK.md) for the
full specification, [CLAUDE.md](CLAUDE.md) for the working rules, and
[DECISIONS.md](DECISIONS.md) for the choices the spec left open.

**Status: Phase 1.** Plain text and `/add` create tasks, `/today`, `/mine` and
`/done` manage them, and anyone outside the whitelist is ignored. Buttons,
reminders, dependencies, templates, the dashboard and documents come in later
phases. The full setup guide (BotFather, systemd, backups) is written in Phase 6.

## Setup

1. Install [uv](https://docs.astral.sh/uv/), then `make sync` to create the
   virtual environment with Python 3.12.
2. In Telegram, talk to [@BotFather](https://t.me/BotFather): `/newbot`, pick a
   name and a username, and copy the token it gives you.
3. Get both Telegram user ids (for example from [@userinfobot](https://t.me/userinfobot)).
4. `cp .env.example .env`, then put the token in `BOT_TOKEN` and both ids in
   `ALLOWED_USER_IDS`, comma-separated. `.env` is gitignored and must never be
   committed.
5. `make run`. The database is created on first start, with mode 0600, and the
   migrations are applied automatically.
6. Send `/start` to the bot in a private chat — that is how it learns where to
   send you direct messages later.
7. Create a private group, add both people and the bot, then in BotFather use
   `/mybots → Bot Settings → Group Privacy → Turn off` so the bot can read plain
   group messages. Without this, only commands reach it.

## Using it

Any plain message becomes a task:

```
@sasha call the landlord about the notice tomorrow #move
```

`@name` or `@me` sets the single owner, `#project` sets the project, and a date —
`today`, `tomorrow`, `mon`…`sun`, `20/09`, `20.09`, `2026-09-20`, `+3d`, `+2w`,
`+1m`, any of them with a time like `14:30` — sets the due date. A date without a
time means 09:00 Paris. There are no priorities: the due date carries the urgency.

Commands: `/add`, `/today`, `/mine`, `/done <id>`, `/help`, `/start`.

## Development

```bash
make test
```

Runs `ruff check`, then `mypy`, then `pytest`. `make lint` checks formatting and
`make fmt` applies it.
