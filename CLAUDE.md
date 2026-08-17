# CLAUDE.md

## What this project is

A shared Telegram task bot for exactly two people (a married couple) who need to
track joint tasks during high-pressure periods — moving apartment and French
administrative paperwork. It is not a product: there are two hardcoded users, one
private group where both of them and the bot live, plus direct messages for
personal reminders and digests. Everything happens inside Telegram, so the design
optimises for low friction (a plain message in the group becomes a task) and low
maintenance (SQLite file, long polling, no web interface, no hosting beyond one
small server). The three features that carry the real value are templates that
expand a checklist into a dated task tree, dependency chains that announce when a
task becomes free, and a document vault holding scans and PDFs attached to tasks.
The full specification is [docs/TASK.md](docs/TASK.md); it wins over this file
whenever the two disagree.

## Stack (section 3 of docs/TASK.md — decided, do not change)

- Python 3.12.
- `aiogram` 3.x for the Telegram API.
- SQLite through the standard library `sqlite3` module with a thin data-access
  layer. No ORM.
- Schema changes are numbered SQL files under `bot/migrations/`. On startup, apply
  every migration whose number is higher than the value in the `schema_version`
  table.
- `APScheduler` 3.x with `AsyncIOScheduler`, no persistent job store. Two kinds of
  fixed job: a tick every 60 seconds, and one cron job per digest. All state lives
  in SQLite, so a restart loses nothing.
- Long polling. No webhook, no domain, no TLS certificate.
- All timestamps stored as UTC in ISO 8601 text; all display in `Europe/Paris`,
  using `zoneinfo` from the standard library.
- `ruff` for lint and format. Full type hints. `mypy` in non-strict mode must pass.
- `pytest` for tests, `freezegun` for anything time-dependent.
- Dependency management with `uv`. `uv.lock` is committed.

## Invariants — never break these

1. **No priority field anywhere.** Not in the schema, not in the code, not in the
   parser, not in a rendered card. The due date carries all urgency. `!word` in a
   message is stripped and answered with "priorities are not supported".
2. **Exactly one owner per task.** `tasks.owner_id` is `NOT NULL` and singular;
   `@us` is rejected with a short explanation. There is no way to assign a task to
   two people, and none may be added.
3. **No notification is ever sent inside quiet hours.** A send that would land in
   the owner's quiet window goes to the `outbox` table with `send_after` set to the
   end of that window. Nothing overrides this — not escalation, not overdue, not a
   digest.

## Code layout rule

Handlers stay thin. A handler parses arguments, calls one function in
`bot/services/` (or `bot/parser.py`), and hands the result to `bot/render.py`. It
holds no business logic, no SQL, and no date arithmetic. All logic lives in
`bot/services/` so the tests can run without Telegram.

## Commands

```bash
make test
```

Runs `ruff check`, then `mypy`, then `pytest`. A phase is not done until this
passes.

```bash
make run
```

Starts the bot with long polling. Requires `BOT_TOKEN` and `ALLOWED_USER_IDS` in
the environment or in a local `.env` file, and fails loudly with a clear message
when either is missing.

## Working rules

- Build in the phases of section 19. Finish a phase, run the tests, commit with the
  phase name, then start the next.
- Do not ask questions. When `docs/TASK.md` leaves a detail open, choose something
  reasonable and add one line to [DECISIONS.md](DECISIONS.md).
- Do not build anything outside the current phase. Other ideas go to
  [IDEAS.md](IDEAS.md).
- Never create a `.env` file and never write a real token into any file, including
  README examples.
