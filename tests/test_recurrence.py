"""Phase 7: a task that comes back after it is closed."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from bot import render
from bot.db import Database
from bot.parser import PARIS
from bot.services.recurrence import Recurrence, next_due, parse_recurrence
from bot.services.tasks import (
    Task,
    append_note,
    complete_task,
    create_task,
    list_subtasks,
    set_recurrence,
)
from bot.services.users import User
from tests.conftest import ALEX_ID, NOW, SASHA_ID


def _task(db: Database, title: str, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=ALEX_ID,
        created_by=ALEX_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


# --- reading the rule ------------------------------------------------------


def test_the_four_shapes_from_the_schema_parse() -> None:
    assert parse_recurrence("daily") == Recurrence("daily")
    assert parse_recurrence("weekly:mon") == Recurrence("weekly", "mon")
    assert parse_recurrence("monthly:15") == Recurrence("monthly", "15")
    assert parse_recurrence("yearly:09-20") == Recurrence("yearly", "09-20")


def test_nonsense_is_refused() -> None:
    assert parse_recurrence("often") is None
    assert parse_recurrence("weekly:funday") is None
    assert parse_recurrence("monthly:41") is None
    assert parse_recurrence("yearly:13-40") is None
    assert parse_recurrence("daily:mon") is None


def test_a_rule_round_trips_through_the_column() -> None:
    for spec in ("daily", "weekly:mon", "monthly:15", "yearly:09-20"):
        rule = parse_recurrence(spec)
        assert rule is not None and rule.stored() == spec


def test_a_rule_can_say_itself_in_words() -> None:
    assert Recurrence("daily").describe() == "every day"
    assert Recurrence("weekly", "mon").describe() == "every Monday"
    assert Recurrence("monthly", "15").describe() == "on the 15th"
    assert Recurrence("yearly", "09-20").describe() == "every 20/9"


# --- when it comes back ----------------------------------------------------


def test_daily_is_the_next_day() -> None:
    assert next_due(Recurrence("daily"), after=date(2026, 9, 15)) == date(2026, 9, 16)


def test_weekly_lands_on_the_named_day() -> None:
    tuesday = date(2026, 9, 15)

    assert next_due(Recurrence("weekly", "mon"), after=tuesday) == date(2026, 9, 21)
    assert next_due(Recurrence("weekly", "tue"), after=tuesday) == date(2026, 9, 22)
    assert next_due(Recurrence("weekly"), after=tuesday) == date(2026, 9, 22)


def test_monthly_lands_on_the_named_date() -> None:
    assert next_due(Recurrence("monthly", "15"), after=date(2026, 9, 15)) == date(2026, 10, 15)
    assert next_due(Recurrence("monthly", "20"), after=date(2026, 9, 15)) == date(2026, 9, 20)
    assert next_due(Recurrence("monthly", "15"), after=date(2026, 12, 15)) == date(2027, 1, 15)


def test_the_31st_of_a_short_month_is_its_last_day() -> None:
    assert next_due(Recurrence("monthly", "31"), after=date(2026, 1, 31)) == date(2026, 2, 28)


def test_yearly_lands_on_the_named_date() -> None:
    assert next_due(Recurrence("yearly", "09-20"), after=date(2026, 9, 15)) == date(2026, 9, 20)
    assert next_due(Recurrence("yearly", "09-20"), after=date(2026, 9, 20)) == date(2027, 9, 20)


# --- closing one and getting the next --------------------------------------


def test_a_weekly_task_reappears_with_the_right_date(db: Database, alex: User) -> None:
    """Phase 7 is done when this passes."""
    monday = datetime(2026, 9, 21, 9, 0, tzinfo=PARIS)
    task = _task(db, "take the bins out", due_at=monday)
    set_recurrence(db, task.id, rule=Recurrence("weekly", "mon"))

    outcome = complete_task(db, task.id, now=monday + timedelta(hours=2))

    assert outcome.task is not None and outcome.task.status == "done"
    assert outcome.next_instance is not None
    assert outcome.next_instance.status == "todo"
    assert outcome.next_instance.due_at is not None
    assert outcome.next_instance.due_at.astimezone(PARIS) == datetime(
        2026, 9, 28, 9, 0, tzinfo=PARIS
    )
    assert outcome.next_instance.recurrence == "weekly:mon"
    assert outcome.next_instance.remind_at == outcome.next_instance.due_at


def test_the_time_of_day_is_kept(db: Database, alex: User) -> None:
    evening = datetime(2026, 9, 15, 18, 30, tzinfo=PARIS)
    task = _task(db, "water the plants", due_at=evening)
    set_recurrence(db, task.id, rule=Recurrence("daily"))

    outcome = complete_task(db, task.id, now=evening)

    assert outcome.next_instance is not None and outcome.next_instance.due_at is not None
    assert outcome.next_instance.due_at.astimezone(PARIS) == datetime(
        2026, 9, 16, 18, 30, tzinfo=PARIS
    )


def test_a_task_that_does_not_repeat_does_not_come_back(db: Database, alex: User) -> None:
    task = _task(db, "book the movers", due_at=NOW)

    outcome = complete_task(db, task.id, now=NOW)

    assert outcome.next_instance is None
    assert len(db.query("SELECT * FROM tasks")) == 1


def test_subtasks_are_copied_and_notes_are_not(db: Database, alex: User, sasha: User) -> None:
    """Section 19, phase 7, exactly: the shape of the job comes, its history does not."""
    monday = datetime(2026, 9, 21, 9, 0, tzinfo=PARIS)
    parent = _task(db, "weekly shop", due_at=monday)
    set_recurrence(db, parent.id, rule=Recurrence("weekly", "mon"))
    append_note(db, parent.id, text="they were out of milk", now=monday)
    create_task(
        db,
        title="buy the bread",
        owner_id=SASHA_ID,
        created_by=ALEX_ID,
        now=monday,
        parent_id=parent.id,
        due_at=monday,
    )

    outcome = complete_task(db, parent.id, now=monday)

    assert outcome.next_instance is not None
    assert outcome.next_instance.notes is None
    children = list_subtasks(db, outcome.next_instance.id)
    assert [child.title for child in children] == ["buy the bread"]
    assert children[0].owner_id == SASHA_ID
    assert children[0].due_at is not None
    assert children[0].due_at.astimezone(PARIS) == datetime(2026, 9, 28, 9, 0, tzinfo=PARIS)


def test_an_undated_repeating_task_counts_from_when_it_was_closed(db: Database, alex: User) -> None:
    task = _task(db, "check the letterbox")
    set_recurrence(db, task.id, rule=Recurrence("daily"))

    outcome = complete_task(db, task.id, now=NOW)

    assert outcome.next_instance is not None and outcome.next_instance.due_at is not None
    assert outcome.next_instance.due_at.astimezone(PARIS).date() == date(2026, 9, 16)


def test_repeating_can_be_switched_off(db: Database, alex: User) -> None:
    task = _task(db, "take the bins out", due_at=NOW)
    set_recurrence(db, task.id, rule=Recurrence("daily"))

    stopped = set_recurrence(db, task.id, rule=None)
    assert stopped is not None and stopped.recurrence is None

    assert complete_task(db, task.id, now=NOW).next_instance is None


def test_a_stored_rule_that_makes_no_sense_is_ignored(db: Database, alex: User) -> None:
    task = _task(db, "odd one", due_at=NOW)
    db.execute("UPDATE tasks SET recurrence = 'whenever' WHERE id = ?", (task.id,))

    assert complete_task(db, task.id, now=NOW).next_instance is None


def test_the_card_says_that_it_repeats(db: Database, alex: User) -> None:
    task = _task(db, "take the bins out", due_at=NOW)
    repeating = set_recurrence(db, task.id, rule=Recurrence("weekly", "mon"))
    assert repeating is not None

    assert "🔁 repeats every Monday" in render.task_card(repeating, alex, now=NOW)
