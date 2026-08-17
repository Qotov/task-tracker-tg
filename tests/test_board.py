"""The tracker board: the counts behind it and the picture it draws."""

from __future__ import annotations

from datetime import datetime, timedelta

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import build_view
from bot.handlers.callbacks import _apply
from bot.parser import PARIS
from bot.services.stats import build_board
from bot.services.tasks import Task, complete_task, create_task, start_waiting
from bot.services.users import User
from tests.conftest import ALEX_ID, NOW, SASHA_ID


def _task(db: Database, title: str, *, owner: int = ALEX_ID, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=owner,
        created_by=ALEX_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def _day(offset: int, hour: int = 9) -> datetime:
    return datetime(2026, 9, 15, hour, 0, tzinfo=PARIS) + timedelta(days=offset)


# --- counting --------------------------------------------------------------


def test_an_empty_board_says_so(db: Database, alex: User) -> None:
    board = build_board(db, now=NOW)

    assert board.is_empty
    assert render.board(board) == render.BOARD_EMPTY


def test_the_board_counts_today_and_the_backlog(db: Database, alex: User, sasha: User) -> None:
    _task(db, "due today", due_at=_day(0, 18))
    _task(db, "late", due_at=_day(-2))
    waiting = _task(db, "waiting on the mairie", due_at=_day(3))
    start_waiting(db, waiting.id, now=NOW)
    finished = _task(db, "already done", due_at=_day(0))
    complete_task(db, finished.id, now=NOW)
    _task(db, "someday")

    board = build_board(db, now=NOW)

    assert board.due_today == 2  # the one due tonight and the late one
    assert board.done_today == 1
    assert board.overdue == 1
    assert board.waiting == 1
    assert board.open_total == 4
    assert board.today_total == 3


def test_the_board_splits_the_load_between_the_two_of_them(
    db: Database, alex: User, sasha: User
) -> None:
    _task(db, "alex tonight", due_at=_day(0, 18))
    _task(db, "sasha tonight", owner=SASHA_ID, due_at=_day(0, 18))
    _task(db, "sasha late", owner=SASHA_ID, due_at=_day(-1))
    done = _task(db, "alex did this", due_at=_day(0))
    complete_task(db, done.id, now=NOW)

    board = build_board(db, now=NOW)
    by_short = {person.user.short: person for person in board.people}

    assert by_short["alex"].done_today == 1
    assert by_short["alex"].due_today == 1
    assert by_short["sasha"].due_today == 2
    assert by_short["sasha"].overdue == 1
    assert by_short["sasha"].done_today == 0


def test_the_week_columns_carry_overdue_work_into_today(db: Database, alex: User) -> None:
    _task(db, "long overdue", due_at=_day(-9))
    _task(db, "tomorrow", due_at=_day(1))
    _task(db, "beyond the horizon", due_at=_day(20))

    board = build_board(db, now=NOW)
    counts = {load.day.isoformat(): load.count for load in board.days}

    assert len(board.days) == 7
    assert counts["2026-09-15"] == 1  # the overdue one is shown as today's problem
    assert counts["2026-09-16"] == 1
    assert sum(counts.values()) == 2  # the far-off one is not in the week


def test_projects_are_ranked_by_size(db: Database, alex: User) -> None:
    for index in range(3):
        _task(db, f"move {index}", project="move")
    _task(db, "visa", project="mother-visa")
    _task(db, "loose end")

    board = build_board(db, now=NOW)

    assert [(load.project, load.count) for load in board.projects] == [
        ("move", 3),
        ("mother-visa", 1),
        (None, 1),
    ]


def test_upcoming_names_the_next_three(db: Database, alex: User) -> None:
    _task(db, "late", due_at=_day(-1))
    for offset in (1, 2, 3, 4):
        _task(db, f"in {offset}", due_at=_day(offset))

    board = build_board(db, now=NOW)

    assert [task.title for task in board.upcoming] == ["in 1", "in 2", "in 3"]


# --- drawing ---------------------------------------------------------------


def test_the_bar_fills_as_the_day_gets_done() -> None:
    assert render.progress_bar(0, 4, width=4) == "▱▱▱▱"
    assert render.progress_bar(2, 4, width=4) == "▰▰▱▱"
    assert render.progress_bar(4, 4, width=4) == "▰▰▰▰"
    assert render.progress_bar(0, 0, width=4) == "▱▱▱▱"


def test_volume_bars_scale_against_the_busiest_row() -> None:
    assert render.volume_bar(4, 4, width=8) == "█" * 8
    assert render.volume_bar(2, 4, width=8) == "█" * 4
    assert render.volume_bar(0, 4, width=8) == ""
    assert render.volume_bar(1, 100, width=8) == "█"  # never disappears entirely


def test_the_board_draws_every_section(db: Database, alex: User, sasha: User) -> None:
    _task(db, "call the mairie", project="mother-visa", due_at=_day(1))
    _task(db, "book the movers", owner=SASHA_ID, project="move", due_at=_day(0, 18))
    late = _task(db, "pay the deposit", due_at=_day(-1))
    done = _task(db, "sign the lease", due_at=_day(0))
    complete_task(db, done.id, now=NOW)
    start_waiting(db, late.id, now=NOW)

    text = render.board(build_board(db, now=NOW))

    assert "📊 <b>Tracker — Tue 15 Sep" in text
    assert "done today" in text
    assert "⚠️ 1 overdue" in text and "⏳ 1 waiting" in text and "📦 3 open" in text
    assert render.board(build_board(db, now=NOW)).count("overdue") == 1
    assert "<b>The week ahead</b>" in text
    assert "<b>Who is carrying what</b>" in text
    assert "<b>Open by project</b>" in text
    assert "<b>Next up</b>" in text
    assert "call the mairie" in text
    assert len(text) < 3000  # section 12 keeps the pinned version under this


def test_the_board_escapes_project_names(db: Database, alex: User) -> None:
    _task(db, "sneaky", project="a<b>bold", due_at=_day(0))

    text = render.board(build_board(db, now=NOW))

    assert "a&lt;b&gt;bold" in text


def test_a_quiet_board_does_not_list_zeroes(db: Database, alex: User) -> None:
    """Nothing late and nothing waiting should read as calm, not as two zeroes."""
    _task(db, "tonight", due_at=_day(0, 18))

    text = render.board(build_board(db, now=NOW))

    assert "overdue" not in text
    assert "waiting" not in text
    assert "📦 1 open" in text


def test_a_single_person_gets_no_comparison_block(db: Database, alex: User) -> None:
    _task(db, "alone", due_at=_day(0))

    text = render.board(build_board(db, now=NOW))

    assert "<b>Who is carrying what</b>" not in text


# --- the views behind the buttons ------------------------------------------


def test_every_menu_view_renders(db: Database, alex: User, sasha: User, config: Config) -> None:
    _task(db, "something", due_at=_day(0))

    for view in ("today", "week", "month", "overdue", "mine", "board", "help", "menu"):
        text, markup = build_view(view, db, user=alex, now=NOW, config=config)
        assert text
        assert markup.inline_keyboard


def test_the_month_view_groups_by_week(
    db: Database, alex: User, sasha: User, config: Config
) -> None:
    _task(db, "this week", due_at=_day(2))
    _task(db, "next week", due_at=_day(9))
    _task(db, "a fortnight out", due_at=_day(20))
    _task(db, "past the horizon", due_at=_day(45))

    text, _ = build_view("month", db, user=alex, now=NOW, config=config)

    assert "<b>This week</b>" in text
    assert "<b>Next week</b>" in text
    assert "<b>Week of Mon 05 Oct</b>" in text
    assert "this week" in text and "a fortnight out" in text
    assert "past the horizon" not in text


def test_the_month_view_says_when_it_is_empty(db: Database, alex: User, config: Config) -> None:
    text, _ = build_view("month", db, user=alex, now=NOW, config=config)

    assert render.NOTHING_THIS_MONTH in text


def test_the_month_view_reaches_further_than_the_week(db: Database, alex: User) -> None:
    from bot.services.tasks import list_month, list_week

    _task(db, "in ten days", due_at=_day(10))

    assert [task.title for task in list_month(db, now=NOW)] == ["in ten days"]
    assert list_week(db, now=NOW) == []


def test_the_reschedule_buttons_keep_the_time_of_day(
    db: Database, alex: User, config: Config
) -> None:
    task = _task(db, "call the bank", due_at=_day(0, 14))

    moved, toast = _apply("when_tomorrow", db, task_id=task.id, now=NOW, config=config)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(PARIS) == datetime(2026, 9, 16, 14, 0, tzinfo=PARIS)
    assert "Wed 16 Sep" in toast


def test_rescheduling_an_undated_task_uses_the_default_hour(
    db: Database, alex: User, config: Config
) -> None:
    task = _task(db, "someday")

    moved, _ = _apply("when_1w", db, task_id=task.id, now=NOW, config=config)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(PARIS) == datetime(2026, 9, 22, 9, 0, tzinfo=PARIS)


def test_the_no_date_button_clears_it(db: Database, alex: User, config: Config) -> None:
    task = _task(db, "call the bank", due_at=_day(0, 14))

    cleared, _ = _apply("when_none", db, task_id=task.id, now=NOW, config=config)

    assert cleared is not None
    assert cleared.due_at is None and cleared.remind_at is None


def test_drop_and_reopen_round_trip(db: Database, alex: User, config: Config) -> None:
    task = _task(db, "never mind", due_at=_day(0))

    dropped, _ = _apply("drop", db, task_id=task.id, now=NOW, config=config)
    assert dropped is not None and dropped.status == "dropped"

    reopened, toast = _apply("reopen", db, task_id=task.id, now=NOW, config=config)
    assert reopened is not None and reopened.status == "todo"
    assert toast == "Reopened"


def test_reopening_a_finished_task_clears_the_done_stamp(
    db: Database, alex: User, config: Config
) -> None:
    task = _task(db, "closed too soon", due_at=_day(0))
    complete_task(db, task.id, now=NOW)

    reopened, _ = _apply("reopen", db, task_id=task.id, now=NOW, config=config)

    assert reopened is not None
    assert reopened.status == "todo"
    assert reopened.done_at is None
