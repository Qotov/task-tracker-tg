# TASK: build a shared Telegram task bot for two people

## 0. How to work on this

- Build in the phases listed in section 12. Finish one phase, run the tests, commit, then start the next.
- Do not ask me questions. If a detail is missing, choose something reasonable and write the choice into `DECISIONS.md` with one line of reasoning.
- Every phase must end with a working bot. Never leave the repo in a state where `make run` fails.
- Write tests as you go. A phase is not done until `make test` passes.
- Do not add features that are not in this file. If you think something is missing, add it to `IDEAS.md` instead of building it.

## 1. Goal

Two people need to track shared tasks inside Telegram. The bot must be useful during high-pressure periods: a move, and a stack of official paperwork with real deadlines.

The bot is not a general product. It has exactly two users. Optimise for low friction and low maintenance.

Three features carry the real value. Do not treat them as extras:

1. **Templates.** One command expands a stored checklist into a full task tree with real dates.
2. **Dependency chains.** Task B is blocked until task A is done. The bot announces when B becomes free.
3. **Document vault.** Scans and PDFs are attached to tasks and searchable, inside Telegram.

## 2. Non-goals

- No web interface.
- No multi-tenant support. Two hardcoded users only.
- No priority field. The due date carries all urgency. Do not add priority in any form.
- No task assigned to two people. Every task has exactly one owner.
- No Kanban board, no time tracking, no tags beyond one project string per task.

## 3. Stack and decisions

These are decided. Do not change them.

- Python 3.12.
- `aiogram` 3.x for the Telegram API.
- SQLite. Use the standard library `sqlite3` module with a thin data-access layer. Do not add an ORM (a library that maps database rows to Python objects — it is unnecessary here).
- Schema changes go in numbered SQL files under `bot/migrations/`. On startup, apply any migration whose number is higher than the value stored in the `schema_version` table.
- `APScheduler` 3.x with `AsyncIOScheduler`. Do not use a persistent job store. Instead schedule two fixed jobs: a tick every 60 seconds, and one cron job per digest. All state lives in SQLite, so a restart loses nothing.
- Long polling. No webhook, no domain, no TLS certificate.
- All timestamps stored as UTC in ISO 8601 text. All display in `Europe/Paris`. Use `zoneinfo` from the standard library.
- `ruff` for lint and format. Full type hints. `mypy` in non-strict mode must pass.
- `pytest` for tests. `freezegun` for time-dependent tests.
- Dependency management with `uv`. Commit `uv.lock`.

## 4. Repository layout

```
bot/
  main.py              entry point, builds Dispatcher, starts scheduler
  config.py            reads env vars, validates them, fails loudly on start
  db.py                connection, migration runner, small query helpers
  migrations/001_init.sql
  parser.py            free-text task parsing
  render.py            all message text building (HTML)
  dashboard.py         pinned dashboard message
  scheduler.py         tick job, digest jobs, quiet-hour queue
  services/
    tasks.py           create, update, complete, block, unblock
    digest.py          builds the morning digest per user
    templates.py       loads YAML templates, expands into tasks
    docs.py            attachment storage and search
    llm.py             optional Claude API fallback parser
  handlers/
    commands.py        slash commands
    callbacks.py       inline button callbacks
    files.py           document and photo intake
    freeform.py        plain text messages and /task on a reply
  middleware/
    whitelist.py       drops updates from unknown users
templates/
  move.yaml
  visa.yaml
  new-arrival.yaml
tests/
scripts/backup.sh
deploy/bot.service
.env.example
Makefile
README.md
DECISIONS.md
```

Keep handlers thin. All logic lives in `services/` and `parser.py`, so tests can run without Telegram.

## 5. Data model

Write this as `bot/migrations/001_init.sql`.

```sql
CREATE TABLE users (
  telegram_id  INTEGER PRIMARY KEY,
  short        TEXT NOT NULL UNIQUE,   -- used for @mentions, lowercase
  display_name TEXT NOT NULL,
  dm_chat_id   INTEGER,                -- filled when the user first DMs the bot
  digest_hour  INTEGER NOT NULL DEFAULT 8,
  quiet_start  TEXT NOT NULL DEFAULT '21:00',
  quiet_end    TEXT NOT NULL DEFAULT '07:30',
  escalation   INTEGER NOT NULL DEFAULT 0   -- 0 off, 1 on
);

CREATE TABLE tasks (
  id           INTEGER PRIMARY KEY,
  parent_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  owner_id     INTEGER NOT NULL REFERENCES users(telegram_id),
  status       TEXT NOT NULL CHECK (status IN ('todo','waiting','done','dropped')),
  project      TEXT,
  due_at       TEXT,      -- UTC ISO 8601
  remind_at    TEXT,      -- UTC ISO 8601
  follow_up_at TEXT,      -- only for status = 'waiting'
  recurrence   TEXT,      -- NULL, 'daily', 'weekly:mon', 'monthly:15', 'yearly:09-20'
  notes        TEXT,
  created_by   INTEGER NOT NULL REFERENCES users(telegram_id),
  created_at   TEXT NOT NULL,
  done_at      TEXT
);

CREATE TABLE task_deps (
  task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on_id)
);

CREATE TABLE attachments (
  id              INTEGER PRIMARY KEY,
  task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
  file_id         TEXT NOT NULL,
  file_unique_id  TEXT NOT NULL,
  file_name       TEXT,
  mime            TEXT,
  kind            TEXT NOT NULL CHECK (kind IN ('document','photo')),
  caption         TEXT,
  added_by        INTEGER NOT NULL REFERENCES users(telegram_id),
  added_at        TEXT NOT NULL
);

CREATE TABLE notifications_sent (
  task_id  INTEGER NOT NULL,
  kind     TEXT NOT NULL,   -- 'remind','overdue','unblocked','followup','escalation'
  day      TEXT NOT NULL,   -- Paris date, YYYY-MM-DD
  PRIMARY KEY (task_id, kind, day)
);

CREATE TABLE outbox (
  id         INTEGER PRIMARY KEY,
  chat_id    INTEGER NOT NULL,
  text       TEXT NOT NULL,
  keyboard   TEXT,          -- JSON, may be NULL
  send_after TEXT NOT NULL, -- UTC ISO 8601
  sent_at    TEXT
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Notes on the model:

- `owner_id` is `NOT NULL`. This enforces the one-owner rule at the database level.
- `notifications_sent` prevents duplicate pings. Check it before every send.
- `outbox` holds messages delayed by quiet hours. The tick job sends anything whose `send_after` has passed.
- `settings` stores the dashboard message id, the group chat id, and similar single values.

## 6. Where the bot runs

- One private Telegram group holds both users and the bot. Group tasks are visible to both.
- The bot also sends direct messages for personal reminders and digests. `dm_chat_id` is captured the first time a user sends `/start` in a private chat.
- The group chat id is stored in `settings` on first use. Refuse to operate in any other group.

## 7. Commands

Task ids are displayed as `#12`. Accept `12` and `#12` in arguments.

| Command | Behaviour |
| --- | --- |
| `/start` | Register the sender if their id is in the whitelist. Store `dm_chat_id` in private chats. Show short help. |
| `/help` | Full command list. |
| `/add <text>` | Parse the text (section 8) and create a task. Reply with the task card. |
| `/task` | Only as a reply to another message. Turns that message text into a task. This must be fast and reliable — it is the main way the second person adds tasks. |
| `/sub <id> <text>` | Create a subtask under `<id>`. Inherit project and owner when the text does not say otherwise. |
| `/done <id>` | Mark done. Runs unblock checks (section 10). |
| `/wait <id> [date]` | Set status `waiting`. Set `follow_up_at`. Default follow-up is 7 days from now. |
| `/drop <id>` | Set status `dropped`. |
| `/due <id> <date>` | Change the due date. |
| `/own <id> @who` | Change owner. |
| `/note <id> <text>` | Append to notes with a date prefix. |
| `/block <id> after <other-id>` | Add a dependency. |
| `/today` | Tasks due today or earlier, both users, grouped by owner. |
| `/week` | Next 7 days. |
| `/overdue` | Everything past due and not done. |
| `/waiting` | All `waiting` tasks with their follow-up dates. |
| `/mine` | Open tasks owned by the sender. |
| `/p <project>` | Full tree for one project, subtasks indented, blocked tasks marked. |
| `/docs <query>` | Search attachments by project, task title, file name, and caption. Send the matches back as files. |
| `/templates` | List available templates with their task counts. |
| `/new <template> <date>` | Expand a template against a target date (section 9). Show a preview with a confirm button before writing anything. |
| `/dash` | Rebuild the dashboard and pin it again. |
| `/export` | Send a CSV of all tasks and a JSON dump. |
| `/settings` | Inline menu for digest hour, quiet hours, escalation on/off. |

## 8. Free-text parsing

Any plain message in the group that is not a command is treated as a task draft. Parse it, then create the task and reply with the card.

Rules, applied to the raw text:

- `@<short>` sets the owner. `@me` means the sender. `@us` is invalid — reply with a short error explaining the one-owner rule.
- `#<word>` sets the project.
- `!<word>` is ignored, and the bot says priorities are not supported.
- Date forms to support: `today`, `tomorrow`, `mon`–`sun` (next occurrence), `20/09`, `20.09`, `2026-09-20`, `+3d`, `+2w`, `+1m`, and any of these followed by a time such as `14:30`. A bare `14:30` means today at that time.
- When a date is given without a time, set `due_at` to 09:00 Paris on that date and `remind_at` equal to `due_at`.
- Remove all matched markers from the title. Strip extra spaces.
- Default owner is the sender. Default project is the project of the last task the sender created in the last 30 minutes, otherwise `NULL`.

Optional LLM fallback in `services/llm.py`: when the parser finds no date and the text is longer than 8 words, and `ANTHROPIC_API_KEY` is set, send the text to the Anthropic API and ask for strict JSON with keys `title`, `owner`, `project`, `due`, `subtasks`. Read the model name from `ANTHROPIC_MODEL` and default it to `claude-haiku-4-5-20251001`. Never let this path block task creation: on any error or timeout over 4 seconds, fall back to the rule-based result. Write a unit test with a mocked HTTP response.

Write `tests/test_parser.py` as a table of at least 30 input strings with expected output. This is the most important test file in the repo.

## 9. Templates

A template is a YAML file in `templates/`. YAML is a plain text format for structured data.

```yaml
name: visa
description: Invitation and visa paperwork for a family visit
project: visa
tasks:
  - key: docs
    title: Collect proof of address and income
    owner: alex
    offset: -60
  - key: mairie
    title: Book mairie appointment for attestation d'accueil
    owner: alex
    offset: -55
    after: [docs]
  - key: attestation
    title: Collect signed attestation d'accueil
    offset: -45
    after: [mairie]
    subtasks:
      - title: Pay the timbre fiscal
      - title: Scan the signed form
  - key: send
    title: Send scanned invitation to her
    offset: -40
    after: [attestation]
```

Rules:

- `offset` is days relative to the target date passed to `/new`. Negative means before.
- `after` lists `key` values and creates rows in `task_deps`.
- `owner` is a user `short`. When absent, the owner is the person who ran `/new`.
- Subtasks inherit owner, project, and the parent offset.
- `/new` first shows a preview: task count, date range, and the first five titles, with **Create** and **Cancel** buttons. Write nothing until Create is pressed.
- If the same template was already expanded for the same target date, warn about the duplicate and still allow it.

Author three real templates:

1. `move.yaml` — moving home: notice to the landlord, movers, deposit, address changes at the bank, insurance, utilities, internet, post redirection, the inspection, school or childcare notification.
2. `visa.yaml` — visitor visa and invitation paperwork for a relative coming from abroad for a month or two.
3. `new-arrival.yaml` — registering a new family member and the paperwork that follows: the birth certificate, the family record, benefits, health-insurance attachment, childcare waiting list, passport.

Mark any step whose exact rules you are unsure about with `verify: true` in the YAML, and render those tasks with a warning symbol. Do not invent legal deadlines with false confidence.

## 10. Dependencies and unblocking

- A task with an unfinished dependency is **blocked**. Show it greyed in listings, at the bottom, with the blocker id.
- Blocked tasks never generate reminders.
- When a task is marked done, check every task that depends on it. If all of its dependencies are now done, send one message to the group: the task title, the owner mention, and the due date. Record `kind='unblocked'` in `notifications_sent`.
- Reject dependency cycles when `/block` is used, and explain why.

## 11. Notifications

The tick job runs every 60 seconds and does four checks. Every send is guarded by `notifications_sent`.

1. **Reminder.** `remind_at` has passed, status is `todo`, task is not blocked. Send to the owner's DM.
2. **Overdue.** `due_at` is in the past and the task is not done. Maximum one message per task per day. After 3 days, stop sending individual messages — overdue items only appear in the digest.
3. **Follow-up.** `follow_up_at` has passed on a `waiting` task. Send to the owner's DM with the text "no answer yet?" and buttons to extend by 7 days or mark done.
4. **Escalation.** Only when the owner has `escalation = 1`. A task overdue by 3 days is mentioned once in the group.

**Quiet hours are absolute.** If a send would fall inside the owner's quiet window, write it to `outbox` with `send_after` set to the end of the window. Nothing overrides this. Write a test that proves a reminder at 23:00 is delivered at 07:30.

**Digest.** One cron job per hour checks which users have `digest_hour` equal to the current Paris hour, then sends that user a DM with: tasks due today, overdue tasks, tasks unblocked in the last 24 hours, and waiting items whose follow-up is today. If all four sections are empty, send nothing.

## 12. Dashboard

One message in the group, pinned, edited in place.

- Content: today's tasks per owner, count of overdue, count of waiting, and the next three upcoming items. Keep it under 3000 characters.
- Store its message id in `settings`. If editing fails because the message was deleted, post a new one and pin it again.
- **Debounce the edits.** Telegram limits how often a bot may write to a group. Coalesce changes and edit at most once every 5 seconds. Implement this as a single asyncio task with a dirty flag. Do not call `edit_message_text` directly from handlers.
- If the text is unchanged, skip the edit. Telegram returns an error for identical content, and that error must not appear in the logs as a failure.

## 13. Inline buttons

Every task card carries buttons. Use `aiogram` callback data with a short prefix and the task id, for example `t:done:12`.

- Todo card: **Done**, **+1 day**, **Give to <partner>**, **Subtask**, **Waiting**.
- Waiting card: **Done**, **+7 days**, **Back to todo**.
- **Subtask** starts a short FSM (a finite state machine — aiogram's way of holding a short multi-step dialogue): the bot asks for the title, the next message becomes the subtask, then the state clears. Add a 5-minute timeout.
- After any button press, update the card in place and answer the callback so the loading spinner stops.

## 14. Attachments

- A document or photo sent to the bot in a private chat, or in the group with no caption command, triggers the intake flow.
- The bot replies with buttons: the 5 most recently touched open tasks, plus **Search** and **Keep without a task**.
- Store `file_id` only. Telegram keeps the file itself. Never download the file to disk.
- `/docs <query>` searches project, task title, file name, and caption, case-insensitive. Send up to 10 matches, each with a caption naming the task.
- When a photo arrives, store the largest available size.

## 15. Security and configuration

- `config.py` reads: `BOT_TOKEN`, `ALLOWED_USER_IDS` (comma-separated), `DB_PATH`, `TZ` (default `Europe/Paris`), `ANTHROPIC_API_KEY` (optional), `ANTHROPIC_MODEL` (optional), `BACKUP_CHAT_ID` (optional). Fail on startup with a clear message when a required value is missing.
- `middleware/whitelist.py` drops every update whose sender id is not in `ALLOWED_USER_IDS`. It sends no reply. Log the sender id at warning level. Anyone can find a bot by its username, and this database holds document numbers.
- Add `tests/test_whitelist.py` proving an unknown user's update never reaches a handler.
- `.env` is in `.gitignore`. Commit `.env.example` with empty values. Never write a token into any committed file, including README examples.
- The database file is created with mode `0600`.

## 16. Backups

`scripts/backup.sh`:

1. `sqlite3 "$DB_PATH" ".backup /tmp/tasks-$(date +%F).db"`
2. gzip it.
3. If `BACKUP_CHAT_ID` is set, upload it with `curl` to `sendDocument` using the bot token from the environment.
4. Delete the local temporary copy. Keep the last 7 gzipped copies on disk.

Document a daily cron entry in the README.

## 17. Deployment

- `deploy/bot.service`: a systemd unit running `uv run python -m bot.main`, with `Restart=always`, `RestartSec=5`, and a dedicated non-root user.
- README section covering: create the bot with BotFather, get both user ids, create the group, add the bot, disable group privacy mode so the bot can read plain messages, run migrations, install the service.

## 18. Testing requirements

- `make test` runs `ruff check`, `mypy`, then `pytest`.
- Unit tests for `parser.py` (at least 30 cases), `services/tasks.py`, `services/templates.py`, `services/digest.py`, and the quiet-hour queue.
- Use an in-memory SQLite database per test with the real migrations applied.
- Use `freezegun` for anything time-based. Include a test crossing a daylight saving change, because the reminder logic converts between UTC and Paris time.
- Coverage of `bot/services/` and `bot/parser.py` at 80% or higher. Handlers need no coverage target.
- No test may make a real network call.

## 19. Build phases

Commit after each phase with a message naming the phase.

**Phase 1 — skeleton and add/list/done.** Config, migrations, whitelist middleware, `/start`, `/help`, `/add`, `/today`, `/mine`, `/done`. Rule-based parser with dates. Tests for the parser and the whitelist.
Done when: both users can add a task by plain text and close it, and unknown senders are ignored.

**Phase 2 — buttons and cards.** `render.py`, inline keyboards, callbacks, subtask FSM, `/sub`, `/due`, `/own`, `/note`, `/drop`, `/week`, `/overdue`.
Done when: a task can be fully managed without typing any command.

**Phase 3 — reminders, digest, quiet hours.** Scheduler, tick job, `notifications_sent`, `outbox`, digest cron, `/settings`.
Done when: the daylight saving test and the 23:00-held-to-07:30 test both pass.

**Phase 4 — waiting and dependencies.** `/wait`, `/block`, follow-up pings, unblock announcements, cycle rejection, blocked rendering in listings.
Done when: closing a blocker posts one unblock message to the group and no duplicate on restart.

**Phase 5 — templates.** `services/templates.py`, `/templates`, `/new` with preview and confirm, and the three real template files.
Done when: `/new visa 2026-12-01` creates the full tree with correct dates and dependencies.

**Phase 6 — dashboard, documents, export, backups.** Pinned dashboard with debounce, attachment intake, `/docs`, `/export`, `scripts/backup.sh`, systemd unit, full README.
Done when: a forwarded PDF is attached to a task and `/docs` returns it.

**Phase 7 — recurrence.** The `recurrence` field. On completion of a recurring task, create the next instance with shifted dates. Subtasks are copied, notes are not.
Done when: a weekly task reappears with the correct next date after being closed.

## 20. Definition of done for the whole task

- `make test` passes.
- `make run` starts the bot against a real token from `.env`.
- README explains setup from zero in numbered steps.
- `DECISIONS.md` lists every choice you made that this file did not specify.
- No priority field exists anywhere in the code or schema.
- No task can be owned by two people.
- No notification can be sent inside quiet hours.
