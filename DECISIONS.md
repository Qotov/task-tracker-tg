# DECISIONS

Every choice `docs/TASK.md` left open, with one line of reasoning each.

## Git layout

- What was wrong: a second, empty git repository had been created *inside* the project (`task-tracker-tg/task-tracker-tg`, a lone `Initial commit` holding only `.gitattributes`); it is no longer on disk, and the project is now one repository whose root is the project folder, with `~/Documents/GitHub` deliberately left as a plain folder so each project keeps its own history.

## Configuration

- Only `BOT_TOKEN` and `ALLOWED_USER_IDS` are required; `DB_PATH` defaults to `./tasks.db`, `TZ` to `Europe/Paris`, `ANTHROPIC_MODEL` to `claude-haiku-4-5-20251001` — a missing optional value should never stop the bot from starting.
- `config.py` reads `.env` itself with a 15-line parser instead of adding `python-dotenv`, because the stack list is fixed and the need is one `KEY=value` loop.
- Values already in the environment win over `.env`, so a systemd unit or a one-off export can override the file.
- `load_config` collects every problem and raises once, so a first run tells you everything that is missing rather than one item per attempt.

## Database

- SQLite access is synchronous: with two users every query is sub-millisecond, and a thread pool would add failure modes for no gain.
- Timestamps are stored without microseconds, so all stored values have the same shape and can be compared and ordered as plain text in SQL.
- `schema_version` holds a single row, created by the migration runner rather than by `001_init.sql`, so migrations never have to know about the bookkeeping table.
- Each migration runs together with its version bump inside one transaction, so an interrupted migration cannot be recorded as applied.
- Mode `0600` is set when the file is created; an existing file's permissions are left alone in case the operator changed them deliberately.

## Users and the `short` handle

- `docs/TASK.md` never says where `users.short` comes from, so it is derived on first sight from the Telegram username (lowercased, non-alphanumerics dropped), falling back to the first name and then to `u<telegram_id>`.
- A `short` that is already taken gets the telegram id appended, because the column is `UNIQUE` and a collision must not crash the second user's first message.
- The `short` is set once and never rewritten afterwards, since templates and `@mentions` refer to it.
- Any handled update registers its sender, not just `/start`, because a task cannot be owned by a user row that does not exist yet.
- `bot/services/users.py` was added to the section 4 layout, which has no home for user registration.

## Parser

- `@word` that is not `@me` and not a registered `short` stays in the title and does not change the owner: it is a mention of a third person, not an assignment.
- The first marker of each kind wins; all `@`, `#` and `!` markers are removed from the title, but only the winning date expression is, so a second date stays as text.
- A weekday name means the next strictly future occurrence, so `mon` written on a Monday is in seven days rather than nine hours ago.
- A bare `20/09` that has already passed rolls to next year, since a task is being created for the future.
- `20/09/2026` (an explicit year) and full weekday names (`monday`) are accepted as natural extensions of the listed forms.
- The dotted form requires a two-digit month (`20.09`, not `20.9`) so that "buy 1.5 kg of flour" is not read as a date.
- An `at`, `on` or `by` immediately before a date or time is swallowed with the marker, otherwise titles end in a dangling preposition.
- `+1m` clamps to the end of a shorter month, so 31 January plus one month is 28 February.
- `remind_at` is set equal to `due_at` even when an explicit time was given, matching the rule stated for dates without a time.
- Expressions that parse but cannot exist (`30/02`, `25:00`) are treated as ordinary text and left in the title.
- A date in one place and a bare time in another ("call the mairie at 14:30 tomorrow") are combined into one due moment.
- `@us` refuses the whole message instead of creating a task with a default owner, so the one-owner rule is visible rather than silently applied.
- A message with no title left after parsing (`#move` alone) is refused with a short error.

## Phase 1 behaviour

- `bot/render.py` exists already, text only, although section 19 lists it under Phase 2: `/add`, `/today` and `/mine` all need message text, and handlers must stay thin.
- Plain text creates a task in a private chat as well as in the group, so the bot is useful before the group is set up.
- `/help` lists only the commands that exist today; advertising unimplemented commands would be a lie.
- `/today` covers open tasks (`todo` and `waiting`) with a due date up to the end of today in Paris; undated tasks never appear there.
- `/mine` lists dated tasks first and undated ones last, because a list sorted by "no date" first buries the urgent items.
- `/done` on an already-closed task says so and leaves the original `done_at` alone.
- Unblock checks on `/done` are left to Phase 4, which is where dependencies are built.
- Binding the group chat id (section 6) is deferred, because Phase 1 lists only the user whitelist and the whitelist already limits the bot to two people.
- The parser rejects a naive `now`, since a bot that mixes naive and aware datetimes fails silently at the daylight saving boundary.

## Testing

- `tests/test_config.py` and `tests/test_tasks.py` were added beyond the two required files, because section 18 asks for unit tests of `services/tasks.py` and configuration failure is the first thing a new operator will hit.
- Coverage is not measured in `make test`: adding `pytest-cov` would extend the fixed dependency list, and the services and parser are tested directly rather than through handlers.
- Async tests call `asyncio.run` instead of adding `pytest-asyncio`, for the same reason.
- The test token in `tests/test_whitelist.py` is a fake string shaped like a Telegram token; no real token exists anywhere in the repository.
