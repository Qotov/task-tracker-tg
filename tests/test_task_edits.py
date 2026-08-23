"""Phase 2 service work: everything a button or an editing command can change."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.db import Database
from bot.parser import DEFAULT_TZ
from bot.services.tasks import (
    append_note,
    back_to_todo,
    create_from_text,
    create_task,
    drop_task,
    list_overdue,
    list_subtasks,
    list_week,
    set_due,
    set_owner,
    shift_due,
    shift_follow_up,
    start_waiting,
)
from bot.services.users import User, partner_of
from tests.conftest import NOW, ROBIN_ID, SAM_ID

# --- dates -----------------------------------------------------------------


def test_set_due_moves_the_reminder_with_it(db: Database, robin: User) -> None:
    task = create_task(db, title="call the mairie", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)

    moved = set_due(db, task.id, due_at=datetime(2026, 9, 20, 7, 0, tzinfo=UTC))

    assert moved is not None
    assert moved.due_at == datetime(2026, 9, 20, 7, 0, tzinfo=UTC)
    assert moved.remind_at == moved.due_at


def test_set_due_can_clear_the_date(db: Database, robin: User) -> None:
    task = create_task(
        db, title="someday", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW, due_at=NOW
    )

    cleared = set_due(db, task.id, due_at=None)

    assert cleared is not None
    assert cleared.due_at is None
    assert cleared.remind_at is None


def test_plus_one_day_keeps_the_time_of_day(db: Database, robin: User) -> None:
    task = create_task(
        db,
        title="call the bank",
        owner_id=ROBIN_ID,
        created_by=ROBIN_ID,
        now=NOW,
        due_at=datetime(2026, 9, 15, 14, 30, tzinfo=DEFAULT_TZ),
    )

    moved = shift_due(db, task.id, days=1, now=NOW)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 9, 16, 14, 30, tzinfo=DEFAULT_TZ)


def test_plus_one_day_survives_the_end_of_summer_time(db: Database, robin: User) -> None:
    """Paris goes from UTC+2 to UTC+1 on 25 October 2026; 09:00 must stay 09:00."""
    before = datetime(2026, 10, 24, 9, 0, tzinfo=DEFAULT_TZ)
    task = create_task(
        db, title="état des lieux", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW, due_at=before
    )

    moved = shift_due(db, task.id, days=1, now=before)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 10, 25, 9, 0, tzinfo=DEFAULT_TZ)
    assert before.utcoffset() != moved.due_at.astimezone(DEFAULT_TZ).utcoffset()
    assert moved.due_at == datetime(2026, 10, 25, 8, 0, tzinfo=UTC)


def test_plus_one_day_on_an_undated_task_means_tomorrow_morning(db: Database, robin: User) -> None:
    task = create_task(db, title="someday", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)

    moved = shift_due(db, task.id, days=1, now=NOW)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 9, 16, 9, 0, tzinfo=DEFAULT_TZ)


def test_shifting_an_unknown_task_returns_nothing(db: Database) -> None:
    assert shift_due(db, 4242, days=1, now=NOW) is None
    assert set_due(db, 4242, due_at=NOW) is None


# --- owner -----------------------------------------------------------------


def test_giving_a_task_away_replaces_the_single_owner(db: Database, robin: User, sam: User) -> None:
    task = create_task(db, title="call the movers", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)

    given = set_owner(db, task.id, owner_id=SAM_ID)

    assert given is not None
    assert given.owner_id == SAM_ID
    assert given.created_by == ROBIN_ID


def test_partner_is_the_other_person(db: Database, robin: User, sam: User) -> None:
    partner = partner_of(db, ROBIN_ID)

    assert partner is not None
    assert partner.telegram_id == SAM_ID


def test_there_is_no_partner_until_the_second_person_starts(db: Database, robin: User) -> None:
    assert partner_of(db, ROBIN_ID) is None


# --- notes, dropping -------------------------------------------------------


def test_notes_are_appended_with_a_date(db: Database, robin: User) -> None:
    task = create_task(db, title="mairie", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)

    first = append_note(db, task.id, text="they asked for a payslip", now=NOW)
    second = append_note(db, task.id, text="sent it", now=NOW + timedelta(days=1))

    assert first is not None and first.notes == "2026-09-15: they asked for a payslip"
    assert second is not None
    assert second.notes == "2026-09-15: they asked for a payslip\n2026-09-16: sent it"


def test_dropping_is_not_the_same_as_doing(db: Database, robin: User) -> None:
    task = create_task(db, title="never mind", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)

    dropped = drop_task(db, task.id)

    assert dropped is not None
    assert dropped.status == "dropped"
    assert dropped.done_at is None


# --- waiting ---------------------------------------------------------------


def test_waiting_sets_a_follow_up_a_week_out(db: Database, robin: User) -> None:
    task = create_task(
        db, title="wait for the mairie", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW
    )

    parked = start_waiting(db, task.id, now=NOW)

    assert parked is not None
    assert parked.status == "waiting"
    assert parked.follow_up_at is not None
    assert parked.follow_up_at.astimezone(DEFAULT_TZ) == datetime(
        2026, 9, 22, 10, 30, tzinfo=DEFAULT_TZ
    )


def test_plus_seven_days_extends_the_follow_up_not_the_due_date(db: Database, robin: User) -> None:
    task = create_task(
        db,
        title="wait for the mairie",
        owner_id=ROBIN_ID,
        created_by=ROBIN_ID,
        now=NOW,
        due_at=NOW,
    )
    start_waiting(db, task.id, now=NOW)

    extended = shift_follow_up(db, task.id, days=7, now=NOW)

    assert extended is not None and extended.follow_up_at is not None
    assert extended.follow_up_at.astimezone(DEFAULT_TZ) == datetime(
        2026, 9, 29, 10, 30, tzinfo=DEFAULT_TZ
    )
    assert extended.due_at == NOW.astimezone(UTC)


def test_back_to_todo_clears_the_follow_up(db: Database, robin: User) -> None:
    task = create_task(db, title="wait", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW)
    start_waiting(db, task.id, now=NOW)

    revived = back_to_todo(db, task.id)

    assert revived is not None
    assert revived.status == "todo"
    assert revived.follow_up_at is None


def test_waiting_tasks_still_count_as_open(db: Database, robin: User) -> None:
    task = create_task(
        db, title="waiting on them", owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW, due_at=NOW
    )
    start_waiting(db, task.id, now=NOW)

    assert [found.id for found in list_overdue(db, now=NOW + timedelta(hours=1))] == [task.id]


# --- subtasks --------------------------------------------------------------


def test_a_subtask_inherits_project_and_owner(db: Database, robin: User, sam: User) -> None:
    parent = create_from_text(db, "@sam #move book the movers", sender=robin, now=NOW).task
    assert parent is not None

    outcome = create_from_text(db, "pay the deposit", sender=robin, now=NOW, parent=parent)

    assert outcome.task is not None
    assert outcome.task.parent_id == parent.id
    assert outcome.task.owner_id == SAM_ID
    assert outcome.task.project == "move"
    assert [child.id for child in list_subtasks(db, parent.id)] == [outcome.task.id]


def test_a_subtask_can_override_the_inherited_owner(db: Database, robin: User, sam: User) -> None:
    parent = create_from_text(db, "@sam #move book the movers", sender=robin, now=NOW).task
    assert parent is not None

    outcome = create_from_text(db, "@me pay the deposit", sender=robin, now=NOW, parent=parent)

    assert outcome.task is not None
    assert outcome.task.owner_id == ROBIN_ID


def test_a_subtask_can_override_the_inherited_project(db: Database, robin: User) -> None:
    parent = create_from_text(db, "#move book the movers", sender=robin, now=NOW).task
    assert parent is not None

    outcome = create_from_text(
        db, "#admin change the address", sender=robin, now=NOW, parent=parent
    )

    assert outcome.task is not None
    assert outcome.task.project == "admin"


# --- listings --------------------------------------------------------------


def test_week_covers_seven_days_from_today(db: Database, robin: User) -> None:
    def dated(title: str, days: int) -> int:
        due = datetime(2026, 9, 15, 9, 0, tzinfo=DEFAULT_TZ) + timedelta(days=days)
        task = create_task(
            db, title=title, owner_id=ROBIN_ID, created_by=ROBIN_ID, now=NOW, due_at=due
        )
        return task.id

    yesterday = dated("yesterday", -1)
    today = dated("today", 0)
    sixth = dated("in six days", 6)
    seventh = dated("in seven days", 7)

    week = [task.id for task in list_week(db, now=NOW)]

    assert today in week
    assert sixth in week
    assert yesterday not in week
    assert seventh not in week


def test_overdue_is_oldest_first_and_skips_finished_work(db: Database, robin: User) -> None:
    older = create_task(
        db,
        title="older",
        owner_id=ROBIN_ID,
        created_by=ROBIN_ID,
        now=NOW,
        due_at=NOW - timedelta(days=3),
    )
    newer = create_task(
        db,
        title="newer",
        owner_id=ROBIN_ID,
        created_by=ROBIN_ID,
        now=NOW,
        due_at=NOW - timedelta(days=1),
    )
    future = create_task(
        db,
        title="later",
        owner_id=ROBIN_ID,
        created_by=ROBIN_ID,
        now=NOW,
        due_at=NOW + timedelta(days=1),
    )
    drop_task(db, future.id)

    assert [task.id for task in list_overdue(db, now=NOW)] == [older.id, newer.id]
