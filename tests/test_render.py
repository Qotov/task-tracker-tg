"""Cards, keyboards and callback payloads (section 13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers.callbacks import _apply
from bot.parser import PARIS
from bot.render import TaskAction
from bot.services.tasks import Task, create_task, start_waiting
from bot.services.users import User, partner_of
from tests.conftest import ALEX_ID, NOW, SASHA_ID


def _make(db: Database, **kwargs: object) -> Task:
    defaults: dict[str, object] = {
        "title": "book the movers",
        "owner_id": ALEX_ID,
        "created_by": ALEX_ID,
        "now": NOW,
    }
    return create_task(db, **{**defaults, **kwargs})  # type: ignore[arg-type]


def _labels(markup: object) -> list[str]:
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


def _payloads(markup: object) -> list[str]:
    assert markup is not None
    return [button.callback_data for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


# --- callback data ---------------------------------------------------------


def test_callback_data_uses_the_short_prefix_from_the_spec() -> None:
    assert TaskAction(action="done", task_id=12).pack() == "t:done:12"


def test_callback_data_round_trips() -> None:
    unpacked = TaskAction.unpack("t:day1:7")

    assert unpacked.action == "day1"
    assert unpacked.task_id == 7


# --- keyboards -------------------------------------------------------------


def test_a_todo_card_can_do_everything_without_typing(
    db: Database, alex: User, sasha: User
) -> None:
    task = _make(db)

    markup = render.task_keyboard(task, partner=partner_of(db, task.owner_id))

    assert _labels(markup) == [
        "✅ Done",
        "📅 +1 day",
        "⏳ Waiting",
        "👤 → sasha",
        "➕ Subtask",
        "👥 Both",
        "📝 Note",
        "🕘 Reschedule",
        "🗑 Drop",
    ]
    assert _payloads(markup) == [
        "t:done:1",
        "t:day1:1",
        "t:wait:1",
        "t:give:1",
        "t:sub:1",
        "t:both:1",
        "t:note:1",
        "t:when:1",
        "t:drop:1",
    ]


def test_the_give_button_disappears_without_a_partner(db: Database, alex: User) -> None:
    task = _make(db)

    markup = render.task_keyboard(task, partner=partner_of(db, task.owner_id))

    assert not any(label.startswith("👤") for label in _labels(markup))
    assert "➕ Subtask" in _labels(markup)


def test_a_waiting_card_carries_its_own_buttons(db: Database, alex: User) -> None:
    task = _make(db)
    waiting = start_waiting(db, task.id, now=NOW)
    assert waiting is not None

    markup = render.task_keyboard(waiting)

    assert _labels(markup) == ["✅ Done", "📅 +7 days", "↩️ To do", "📝 Note", "🗑 Drop"]
    assert _payloads(markup)[:3] == ["t:done:1", "t:day7:1", "t:todo:1"]


def test_a_closed_card_keeps_only_reopen(db: Database, alex: User) -> None:
    """Closing the wrong task with a thumb must be undoable with a thumb."""
    from bot.services.tasks import complete_task, drop_task

    task = _make(db)
    done = complete_task(db, task.id, now=NOW).task
    assert done is not None
    assert _labels(render.task_keyboard(done)) == ["↩️ Reopen"]

    other = _make(db, title="never mind")
    dropped = drop_task(db, other.id)
    assert dropped is not None
    assert _payloads(render.task_keyboard(dropped)) == ["t:reopen:2"]


def test_the_reschedule_row_offers_dates_and_a_way_back(db: Database, alex: User) -> None:
    task = _make(db)

    markup = render.reschedule_keyboard(task)

    assert _labels(markup) == [
        "Today",
        "Tomorrow",
        "+3 days",
        "Next week",
        "+1 month",
        "+3 months",
        "✖️ No date",
        "← Back",
    ]
    assert "t:when_tomorrow:1" in _payloads(markup)
    assert "t:when_1m:1" in _payloads(markup)
    assert "t:when_3m:1" in _payloads(markup)
    assert "t:when_back:1" in _payloads(markup)


def test_the_menu_leads_with_adding_and_reaches_every_view() -> None:
    markup = render.menu_keyboard()

    assert _payloads(markup) == [
        "m:new",
        "m:today",
        "m:week",
        "m:month",
        "m:overdue",
        "m:mine",
        "m:board",
        "m:help",
    ]
    assert _labels(markup)[0] == "➕ New task"


def test_a_prompt_can_be_cancelled() -> None:
    assert _payloads(render.cancel_keyboard()) == ["m:cancel"]


def test_a_list_offers_one_button_per_task(db: Database, alex: User) -> None:
    tasks = [_make(db, title=f"task {index}") for index in range(10)]

    markup = render.list_keyboard(tasks, view="today")

    openers = [payload for payload in _payloads(markup) if payload.startswith("t:open:")]
    assert len(openers) == render.OPENABLE_IN_LIST
    assert openers[0] == f"t:open:{tasks[0].id}"
    assert _payloads(markup)[-2:] == ["m:today", "m:menu"]


def test_the_home_keyboard_labels_match_the_views() -> None:
    from bot.handlers.commands import HOME_BUTTONS

    labels = [button.text for row in render.home_keyboard().keyboard for button in row]

    assert set(labels) == set(HOME_BUTTONS)


# --- cards -----------------------------------------------------------------


def test_a_card_names_owner_date_and_project(db: Database, alex: User) -> None:
    task = _make(db, project="move", due_at=datetime(2026, 9, 20, 9, 0, tzinfo=PARIS))

    text = render.task_card(task, alex, now=NOW)

    assert "#1 <b>book the movers</b>" in text
    assert "👤 alex" in text
    assert "📅 Sun 20 Sep 09:00" in text
    assert "🏷 #move" in text


def test_a_card_shows_the_follow_up_when_waiting(db: Database, alex: User) -> None:
    task = _make(db)
    waiting = start_waiting(db, task.id, now=NOW)
    assert waiting is not None

    text = render.task_card(waiting, alex, now=NOW)

    assert "⏳ waiting" in text
    assert "Tue 22 Sep" in text


def test_a_card_points_at_its_parent(db: Database, alex: User) -> None:
    parent = _make(db)
    child = _make(db, title="pay the deposit", parent_id=parent.id)

    assert "↳ subtask of #1" in render.task_card(child, alex, now=NOW)


def test_a_card_shows_notes_and_escapes_them(db: Database, alex: User) -> None:
    from bot.services.tasks import append_note

    task = _make(db)
    noted = append_note(db, task.id, text="ask about <b>the deposit</b>", now=NOW)
    assert noted is not None

    text = render.task_card(noted, alex, now=NOW)

    assert "📝 2026-09-15: ask about &lt;b&gt;the deposit&lt;/b&gt;" in text


def test_an_overdue_card_is_marked(db: Database, alex: User) -> None:
    task = _make(db, due_at=NOW - timedelta(days=1))

    assert "⚠️" in render.task_card(task, alex, now=NOW)


# --- lists -----------------------------------------------------------------


def test_the_week_list_groups_by_day(db: Database, alex: User, sasha: User) -> None:
    from bot.services.tasks import list_week

    _make(db, title="movers", due_at=datetime(2026, 9, 16, 9, 0, tzinfo=PARIS))
    _make(db, title="mairie", owner_id=SASHA_ID, due_at=datetime(2026, 9, 16, 14, 0, tzinfo=PARIS))
    _make(db, title="bank", due_at=datetime(2026, 9, 18, 9, 0, tzinfo=PARIS))

    text = render.week_list(
        list_week(db, now=NOW), {alex.telegram_id: alex, sasha.telegram_id: sasha}, now=NOW
    )

    assert "<b>Wed 16 Sep</b>" in text
    assert "<b>Fri 18 Sep</b>" in text
    assert "09:00 · alex" in text
    assert "14:00 · sasha" in text


def test_empty_lists_say_so(db: Database, alex: User) -> None:
    assert render.NOTHING_THIS_WEEK in render.week_list([], {}, now=NOW)
    assert render.NOTHING_OVERDUE in render.overdue_list([], {}, now=NOW)
    assert render.NOTHING_TODAY in render.today_list([], {}, now=NOW)
    assert render.NOTHING_OPEN in render.open_list([], title="Open tasks", now=NOW)


def test_the_help_never_advertises_priorities() -> None:
    text = render.help_text()

    assert "no priorities" in text
    assert "/week" in text and "/sub" in text and "/note" in text


# --- what the buttons do ---------------------------------------------------


def test_every_button_action_does_what_it_says(
    db: Database, alex: User, sasha: User, config: Config
) -> None:
    task = _make(db, due_at=datetime(2026, 9, 20, 9, 0, tzinfo=PARIS))
    now = NOW

    moved, _ = _apply("day1", db, task_id=task.id, now=now, config=config)
    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(PARIS) == datetime(2026, 9, 21, 9, 0, tzinfo=PARIS)

    given, toast = _apply("give", db, task_id=task.id, now=now, config=config)
    assert given is not None and given.owner_id == SASHA_ID
    assert "sasha" in toast

    parked, _ = _apply("wait", db, task_id=task.id, now=now, config=config)
    assert parked is not None and parked.status == "waiting"

    extended, _ = _apply("day7", db, task_id=task.id, now=now, config=config)
    assert extended is not None and extended.follow_up_at is not None
    assert extended.follow_up_at.astimezone(PARIS) == datetime(2026, 9, 29, 10, 30, tzinfo=PARIS)

    revived, _ = _apply("todo", db, task_id=task.id, now=now, config=config)
    assert revived is not None and revived.status == "todo"

    finished, _ = _apply("done", db, task_id=task.id, now=now, config=config)
    assert finished is not None and finished.status == "done"
    assert finished.done_at == now.astimezone(UTC)


def test_pressing_done_twice_is_reported_not_repeated(
    db: Database, alex: User, config: Config
) -> None:
    task = _make(db)
    _apply("done", db, task_id=task.id, now=NOW, config=config)

    again, toast = _apply("done", db, task_id=task.id, now=NOW + timedelta(hours=1), config=config)

    assert again is not None and again.done_at == NOW.astimezone(UTC)
    assert toast == "Already done"


def test_giving_away_without_a_partner_explains_itself(
    db: Database, alex: User, config: Config
) -> None:
    task = _make(db)

    given, toast = _apply("give", db, task_id=task.id, now=NOW, config=config)

    assert given is None
    assert toast == render.NO_PARTNER


def test_an_unknown_action_changes_nothing(db: Database, alex: User, config: Config) -> None:
    task = _make(db)

    changed, toast = _apply("nonsense", db, task_id=task.id, now=NOW, config=config)

    assert changed is None
    assert "button" in toast
