# PLAN

## Adding and a month view — done

Asked for directly: a way to add a task from the menu, and a month alongside the
week.

| File | What changed |
| --- | --- |
| `bot/render.py` | `➕ New task` leading the menu, `📆 Month` in both keyboards, `month_list` grouped by week, the guided prompt and its Cancel button. |
| `bot/handlers/callbacks.py` | `start_new_task`, the `new` and `cancel` menu actions, and the `new` branch of the prompt answer. |
| `bot/handlers/commands.py` | `/new`, `/month`, and the two new home-keyboard labels. |
| `bot/services/tasks.py` | `list_month` and `list_ahead`, shared with `list_week`. |

## Two-people pass — done

Asked for directly: month-scale reschedule buttons, whatever helps when two
people share the same lists, and something useful that a calendar knows.

| File | What changed |
| --- | --- |
| `bot/services/holidays.py` | New. French public holidays, computed (fixed dates plus Easter), no network. |
| `bot/services/tasks.py` | `find_similar` (duplicate hint) and `copy_for` (one task each for a shared errand). |
| `bot/render.py` | `+1 month` / `+3 months`, the `👥 Both` button, *asked by* on a card, the `jour férié` warning, `duplicate_hint`. |
| `bot/handlers/callbacks.py` | Month reschedules and the `Both` copy. |
| `bot/parser.py` | `add_months` made public for the month buttons. |
| `tests/test_together.py` | New. Months, attribution, duplicates, twins, holidays. |

## Interface pass — done

Asked for directly: better card buttons, buttons from the moment the bot starts,
and a visual way to track the work. Outside the phase order; the pinned,
debounced dashboard of section 12 stays in Phase 6.

| File | What changed |
| --- | --- |
| `bot/services/stats.py` | New. Counts today, the week ahead, per person, per project, what is next. |
| `bot/render.py` | The board (progress and volume bars), the menu, the reschedule row, the home keyboard, richer cards. |
| `bot/handlers/callbacks.py` | Menu navigation, open-from-list, reschedule submenu, note dialogue, drop and reopen. |
| `bot/handlers/commands.py` | `/menu`, `/board`, the home-keyboard labels, and all four list commands sharing one view builder. |
| `bot/handlers/__init__.py` | `build_view`, so a command and a button render the same thing. |
| `tests/test_board.py` | New. The counting, the drawing, and every new button. |

## Phase 2 (buttons and cards) — done

Scope: `render.py` keyboards, inline callbacks, the subtask dialogue, `/sub`,
`/due`, `/own`, `/note`, `/drop`, `/week`, `/overdue`. Done when a task can be
fully managed without typing a command.

| File | What changed |
| --- | --- |
| `bot/render.py` | `TaskAction` callback data, `task_keyboard`, richer cards (waiting, parent, notes), `week_list` and `overdue_list`. |
| `bot/handlers/callbacks.py` | New. One handler for every button, plus the five-minute subtask dialogue. |
| `bot/handlers/commands.py` | `/sub`, `/due`, `/own`, `/note`, `/drop`, `/week`, `/overdue`; every card now carries buttons. |
| `bot/handlers/__init__.py` | `send_card`, `refresh_card` (in-place edits) and `answer_creation`. |
| `bot/services/tasks.py` | `set_due`, `shift_due`, `set_owner`, `append_note`, `drop_task`, `start_waiting`, `shift_follow_up`, `back_to_todo`, `list_subtasks`, `list_week`, `list_overdue`. |
| `bot/services/users.py` | `partner_of`, for the "Give to …" button. |
| `bot/parser.py` | `default_owner` so subtasks inherit, and a public `parse_when` for `/due`. |
| `bot/main.py` | `MemoryStorage` for the FSM; the callbacks router sits between commands and freeform. |
| `tests/test_task_edits.py` | New. Every edit, including a daylight-saving crossing. |
| `tests/test_render.py` | New. Cards, keyboards, callback payloads, and what each button does. |

## Phase 1 (skeleton and add/list/done) — done

Scope: config, migration 001, whitelist middleware, `/start`, `/help`, `/add`,
`/today`, `/mine`, `/done`, plain-text task creation, the rule-based parser.
Done when both users can add a task by plain text and close it, and unknown
senders are ignored.

## Files

| File | What it does |
| --- | --- |
| `bot/config.py` | Reads and validates env vars (loads `.env` if present), fails loudly on start. |
| `bot/migrations/001_init.sql` | The full schema from section 5, verbatim. |
| `bot/db.py` | Connection (mode 0600, foreign keys on), migration runner, small query helpers. |
| `bot/parser.py` | Rule-based free-text parsing: owner, project, date/time, priority rejection. |
| `bot/services/users.py` | Registers a whitelisted sender, derives their `short`, stores `dm_chat_id`. |
| `bot/services/tasks.py` | Create a task from parsed text, complete it, list today / mine / one task. |
| `bot/render.py` | Builds every message string (HTML): task card, list sections, help text. |
| `bot/middleware/whitelist.py` | Drops updates from senders outside `ALLOWED_USER_IDS`, silently, with a warning log. |
| `bot/handlers/commands.py` | `/start`, `/help`, `/add`, `/today`, `/mine`, `/done` — argument parsing only. |
| `bot/handlers/freeform.py` | Plain text messages become task drafts. |
| `bot/main.py` | Entry point: config, migrations, Dispatcher, middleware, routers, long polling. |
| `tests/conftest.py` | In-memory SQLite with the real migrations applied; fixed clock helpers. |
| `tests/test_parser.py` | Table of 30+ input strings with expected owner / project / due / title. |
| `tests/test_whitelist.py` | Proves an unknown sender's update never reaches a handler. |
| `tests/test_config.py` | Proves a missing `BOT_TOKEN` fails with a clear message. |
| `tests/test_tasks.py` | Create / complete / list against the real schema. |

## Build order

1. `config.py` + `tests/test_config.py` — nothing can start without it.
2. `migrations/001_init.sql` + `db.py` + `tests/conftest.py` — schema and the test fixture everything else uses.
3. `parser.py` + `tests/test_parser.py` — the most important test file; pure, no DB, no Telegram.
4. `services/users.py`, `services/tasks.py` + `tests/test_tasks.py` — the logic layer.
5. `render.py` — message strings, so handlers carry none.
6. `middleware/whitelist.py` + `tests/test_whitelist.py` — the security gate.
7. `handlers/commands.py`, `handlers/freeform.py`, `main.py` — thin wiring on top.
8. `make test`, then verify `make run` fails clearly without `BOT_TOKEN`.
