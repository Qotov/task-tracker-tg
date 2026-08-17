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

## Phase 2 behaviour

- The section 13 waiting buttons (**Waiting**, **+7 days**, **Back to todo**) are built now even though the `/wait` command belongs to Phase 4, because Phase 2 is done only when a task can be managed without typing a command, and otherwise the waiting status would be unreachable.
- Inline keyboards live in `render.py` next to the text: they are presentation, and section 4 gives them no module of their own.
- Callback payloads are built with aiogram's `CallbackData` factory so they render exactly as section 13 asks — `t:done:12`.
- **+1 day** on a todo card moves the due date, while **+7 days** on a waiting card moves the follow-up, because that is the date a waiting card is about.
- Day arithmetic keeps the wall-clock time across a daylight saving change: a task due 09:00 stays due 09:00, which is what a person means by "one more day".
- **+1 day** on a task with no due date means tomorrow at 09:00 rather than this time tomorrow.
- **Give to …** is hidden until the second person has sent their first message, since there is nobody to give the task to yet.
- A subtask inherits its parent's owner and project, so the parser gained a `default_owner` separate from the sender; `@me` still means whoever is typing.
- The subtask prompt expires after five minutes and a late answer becomes an ordinary task, so nothing anybody typed is silently thrown away.
- The FSM uses in-memory storage: a restart forgets a half-finished subtask prompt, which costs one retyped message and saves a storage dependency.
- Dropping a task leaves `done_at` empty — dropped is not done, and later phases must be able to tell them apart.
- `/week` covers seven days counting today and leaves anything earlier to `/overdue`, so the two lists do not repeat each other.
- `/due` accepts every date form a task message does, through a new public `parse_when` in the parser.
- An edit that would not change a card is swallowed, because Telegram answers an identical edit with an error that is not a failure.

## Interface pass (asked for directly, outside the phase list)

- Richer buttons, a menu and a tracker board were requested directly, so they were built ahead of the phase order; the *pinned, debounced* dashboard of section 12 is still Phase 6, and this board is an on-demand `/board` instead.
- Card buttons go beyond the section 13 list (**Note**, **Reschedule**, **Drop**, and **Reopen**), because a card you can only close or postpone still leaves everything else to typed commands.
- A closed card keeps one button — **Reopen** — since closing the wrong task with a thumb is the easiest mistake to make here, and until now nothing could undo it.
- **Reschedule** swaps the buttons on the same message rather than posting a new one, so a card stays a single message in the chat.
- A reschedule keeps the task's existing time of day (14:30 today becomes 14:30 tomorrow) and falls back to the usual 09:00 for a task that had no date.
- The keyboard under the text field is attached only in private chats: Telegram shows a reply keyboard to everybody in a group, and two people do not need two copies of it.
- Its labels start with an emoji (`📅 Today`), so a typed task can never be mistaken for a button press.
- The menu rewrites one message in place as you move between views, so the chat does not fill with lists.
- Every list carries one `#id` button per task, up to eight, so a task can be opened and acted on without typing its number.
- The board counts everything in one pass over the task table in Python rather than in six SQL aggregates: at two users it is faster to read and cheaper to change.
- Overdue work is drawn in today's column of the week chart, because today is when it has to be dealt with.
- Counters that are zero are left out — "nothing late" should look calm rather than like two zeroes.
- Bars are block characters inside `<pre>`, the only way to get columns to line up in Telegram.
- `bot/services/stats.py` holds the counting and `render.py` the drawing, so the Phase 6 pinned dashboard can reuse both.
- The board ranks by due date and count only; there is still no priority anywhere in this bot.

## Working as two people (asked for directly)

- **Reschedule** gained **+1 month** and **+3 months**, because French paperwork is measured in months — a `titre de séjour` renewal or a mairie backlog is never three days away.
- Month arithmetic reuses the parser's `add_months`, so 31 August plus three months is 30 November rather than spilling into December.
- A card says **asked by <name>** when the person who wrote it is not the person who owns it: with two people the real question is not what the task is, it is who put it there.
- **👥 Both** copies a task onto the other person's list instead of sharing one task, because `owner_id` is singular and stays that way; two signatures at the mairie are two tasks.
- The copy is an independent task: closing one leaves the other open, which is the whole point of tracking them separately.
- Adding something that reads like an already-open task gets a hint naming the older one, since two people in two rooms writing "call the landlord" is this bot's most likely duplicate.
- The hint never blocks the second task — being wrong about a duplicate must cost nothing.
- Similarity is `difflib` on lowercased, punctuation-free titles at 0.82, and titles under six characters are skipped; a standard-library ratio is predictable and needs no dependency.
- French public holidays are computed locally (fixed dates plus Easter by Meeus/Jones/Butcher) rather than fetched: the rules have not changed since 1953, and a card that needs the network to render is a card that fails on a train.
- Only public holidays are flagged, not weekends: a Saturday task is ordinary, a task due on the 14th of July is a wasted trip.
- The holiday names stay in French (`Fête nationale`), because that is what the closed door and the mairie website will say.

## Adding from the menu, and a month view (asked for directly)

- **➕ New task** leads the menu, on its own row: adding is the thing this bot exists for, and it should not sit below six ways of looking at what already exists.
- The button asks once and then hands the answer to the ordinary parser, so `@sasha call the landlord tomorrow #move` works exactly as it does when typed — one flow to learn, not two.
- The prompt lists the markers with an example rather than walking through owner, project and date as separate questions: four taps to add a task is worse than one sentence.
- The prompt carries a **Cancel** button, because changing your mind should not require sending a message that becomes a task.
- A late answer to that prompt still becomes a task, with no expiry: typing it in a private chat would have made one anyway, so throwing it away would be the surprising choice.
- **📆 Month** joins Today and Week in both keyboards, covering thirty days from today.
- The month is grouped by week, not by day: thirty day-headers is a wall, and "This week / Next week / Week of Mon 05 Oct" is how the two of them already talk about it.
- `/week` and `/month` share one `list_ahead` query, so the two views can never disagree about what "open and dated" means.
- The keyboard under the text field grew to three rows of three; **➕ New task** sits on the last row next to the menu, where a thumb reaches it.

## Fixes found in live use

- A bot is never written to `users`, and button presses register `callback.from_user` rather than `callback.message.from_user`: the message carrying the buttons was sent by the bot, so the old code registered the bot itself as a person, offered it in **Give to**, and let three tasks end up owned by it.
- Month names are now understood — `Sep 24`, `24 Sep`, `24 September 14:30`, `24 sep 2027` — because a real message ("book movers2 Sep 24") produced no date at all, and `24 September 14:30` was worse: it took the time and quietly filed the task for today.
- A month name only counts as a date when a day number sits next to it, so "march to the mairie" and "the movers may come" stay ordinary words.
- A named month with no year rolls forward exactly like `20/09` does, so the rule is the same wherever a day and a month appear without one.

## Testing

- `tests/test_config.py` and `tests/test_tasks.py` were added beyond the two required files, because section 18 asks for unit tests of `services/tasks.py` and configuration failure is the first thing a new operator will hit.
- Coverage is not measured in `make test`: adding `pytest-cov` would extend the fixed dependency list, and the services and parser are tested directly rather than through handlers.
- Async tests call `asyncio.run` instead of adding `pytest-asyncio`, for the same reason.
- The test token in `tests/test_whitelist.py` is a fake string shaped like a Telegram token; no real token exists anywhere in the repository.
