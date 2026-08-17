# task-tracker-tg

A shared Telegram task bot for two people. See [docs/TASK.md](docs/TASK.md) for the
full specification, [CLAUDE.md](CLAUDE.md) for the working rules, and
[DECISIONS.md](DECISIONS.md) for the choices the spec left open.

**Status: Phase 2.** Plain text and `/add` create tasks, every card carries
buttons, and `/sub`, `/due`, `/own`, `/note`, `/drop`, `/done`, `/today`,
`/week`, `/overdue` and `/mine` manage them. Anyone outside the whitelist is
ignored. Reminders, dependencies, templates, the dashboard and documents come in
later phases. The full setup guide (BotFather, systemd, backups) is written in
Phase 6.

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

Everything else is buttons. A todo card carries **Done**, **+1 day**,
**Waiting**, **→ partner**, **Subtask**, **Note**, **Reschedule** and **Drop**; a
waiting card swaps in **+7 days** and **To do**; a closed card keeps **Reopen**,
because closing the wrong task with a thumb is easy. **Reschedule** opens
Today / Tomorrow / +3 days / Next week / No date on the same card.

In a private chat a keyboard sits under the text field with **Today**, **Week**,
**Overdue**, **Mine**, **Board** and **Menu**. Every list also puts an `#id`
button under it, so you can open a task and act on it without typing its number.

`/board` draws the tracker: how much of today is done, what is late or waiting,
a bar chart of the next seven days, who is carrying what, open work per project,
and the next three things due.

Commands: `/menu`, `/board`, `/add`, `/sub`, `/done`, `/drop`, `/due`, `/own`,
`/note`, `/today`, `/week`, `/overdue`, `/mine`, `/help`, `/start`.

## Development

```bash
make test
```

Runs `ruff check`, then `mypy`, then `pytest`. `make lint` checks formatting and
`make fmt` applies it.
