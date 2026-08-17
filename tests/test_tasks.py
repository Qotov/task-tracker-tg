"""The task service, against the real schema in an in-memory database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from bot.db import Database
from bot.parser import PARIS
from bot.services.tasks import (
    complete_task,
    create_from_text,
    create_task,
    list_due_today,
    list_open_for,
    recent_project,
)
from bot.services.users import User, ensure_user, get_user, known_shorts
from tests.conftest import ALEX_ID, NOW, SASHA_ID


def test_a_plain_message_becomes_a_task(db: Database, alex: User) -> None:
    outcome = create_from_text(db, "book the movers #move tomorrow", sender=alex, now=NOW)

    assert outcome.error is None
    assert outcome.task is not None
    assert outcome.task.title == "book the movers"
    assert outcome.task.project == "move"
    assert outcome.task.status == "todo"
    assert outcome.task.owner_id == ALEX_ID
    assert outcome.task.created_by == ALEX_ID
    assert outcome.task.due_at == datetime(2026, 9, 16, 7, 0, tzinfo=UTC)
    assert outcome.task.remind_at == outcome.task.due_at


def test_owner_marker_gives_the_task_to_the_other_person(
    db: Database, alex: User, sasha: User
) -> None:
    outcome = create_from_text(db, "@sasha call the landlord", sender=alex, now=NOW)

    assert outcome.task is not None
    assert outcome.task.owner_id == SASHA_ID
    assert outcome.task.created_by == ALEX_ID


def test_us_creates_nothing(db: Database, alex: User, sasha: User) -> None:
    outcome = create_from_text(db, "@us do the dishes", sender=alex, now=NOW)

    assert outcome.task is None
    assert outcome.error is not None
    assert "one owner" in outcome.error
    assert db.query("SELECT * FROM tasks") == []


def test_priority_marker_is_dropped_but_the_task_is_created(db: Database, alex: User) -> None:
    outcome = create_from_text(db, "!urgent fix the boiler", sender=alex, now=NOW)

    assert outcome.task is not None
    assert outcome.task.title == "fix the boiler"
    assert outcome.warnings


def test_project_is_remembered_for_thirty_minutes(db: Database, alex: User) -> None:
    with freeze_time("2026-09-15T08:00:00+00:00"):
        create_from_text(db, "#move pack the kitchen", sender=alex, now=datetime.now(UTC))

    with freeze_time("2026-09-15T08:20:00+00:00"):
        soon = create_from_text(db, "book the van", sender=alex, now=datetime.now(UTC))

    with freeze_time("2026-09-15T09:00:00+00:00"):
        later = create_from_text(db, "call the concierge", sender=alex, now=datetime.now(UTC))

    assert soon.task is not None and soon.task.project == "move"
    assert later.task is not None and later.task.project is None


def test_project_memory_is_per_person(db: Database, alex: User, sasha: User) -> None:
    create_from_text(db, "#move pack the kitchen", sender=alex, now=NOW)

    outcome = create_from_text(db, "book the van", sender=sasha, now=NOW + timedelta(minutes=5))

    assert outcome.task is not None
    assert outcome.task.project is None
    assert recent_project(db, SASHA_ID, now=NOW) is None


def test_completing_a_task_records_when(db: Database, alex: User) -> None:
    created = create_task(
        db, title="pay the deposit", owner_id=ALEX_ID, created_by=ALEX_ID, now=NOW
    )

    outcome = complete_task(db, created.id, now=NOW + timedelta(hours=1))

    assert outcome.already_done is False
    assert outcome.task is not None
    assert outcome.task.status == "done"
    assert outcome.task.done_at == (NOW + timedelta(hours=1)).astimezone(UTC)


def test_completing_twice_is_reported(db: Database, alex: User) -> None:
    created = create_task(
        db, title="pay the deposit", owner_id=ALEX_ID, created_by=ALEX_ID, now=NOW
    )
    complete_task(db, created.id, now=NOW)

    outcome = complete_task(db, created.id, now=NOW + timedelta(hours=2))

    assert outcome.already_done is True
    assert outcome.task is not None
    assert outcome.task.done_at == NOW.astimezone(UTC)


def test_completing_an_unknown_task_reports_nothing_found(db: Database) -> None:
    outcome = complete_task(db, 4242, now=NOW)

    assert outcome.task is None


def test_today_covers_overdue_and_today_for_both_people(
    db: Database, alex: User, sasha: User
) -> None:
    yesterday = create_task(
        db,
        title="overdue",
        owner_id=ALEX_ID,
        created_by=ALEX_ID,
        now=NOW,
        due_at=NOW - timedelta(days=1),
    )
    later_today = create_task(
        db,
        title="tonight",
        owner_id=SASHA_ID,
        created_by=ALEX_ID,
        now=NOW,
        due_at=datetime(2026, 9, 15, 20, 0, tzinfo=PARIS),
    )
    create_task(
        db,
        title="tomorrow",
        owner_id=ALEX_ID,
        created_by=ALEX_ID,
        now=NOW,
        due_at=NOW + timedelta(days=1),
    )
    create_task(db, title="someday", owner_id=ALEX_ID, created_by=ALEX_ID, now=NOW)

    due = list_due_today(db, now=NOW)

    assert [task.id for task in due] == [yesterday.id, later_today.id]


def test_today_ignores_finished_tasks(db: Database, alex: User) -> None:
    done = create_task(
        db, title="done already", owner_id=ALEX_ID, created_by=ALEX_ID, now=NOW, due_at=NOW
    )
    complete_task(db, done.id, now=NOW)

    assert list_due_today(db, now=NOW) == []


def test_mine_lists_open_tasks_dated_first(db: Database, alex: User, sasha: User) -> None:
    undated = create_task(db, title="someday", owner_id=ALEX_ID, created_by=ALEX_ID, now=NOW)
    dated = create_task(
        db,
        title="friday",
        owner_id=ALEX_ID,
        created_by=ALEX_ID,
        now=NOW,
        due_at=NOW + timedelta(days=3),
    )
    create_task(db, title="not mine", owner_id=SASHA_ID, created_by=ALEX_ID, now=NOW)

    assert [task.id for task in list_open_for(db, ALEX_ID)] == [dated.id, undated.id]


def test_a_new_user_is_registered_once(db: Database) -> None:
    first = ensure_user(db, telegram_id=ALEX_ID, username="Alex", first_name="Alex")
    again = ensure_user(db, telegram_id=ALEX_ID, username="Alex", first_name="Alex")

    assert first.short == "alex"
    assert again.short == "alex"
    assert len(db.query("SELECT * FROM users")) == 1


def test_dm_chat_id_is_captured_on_the_first_private_message(db: Database) -> None:
    ensure_user(db, telegram_id=ALEX_ID, username="alex")

    updated = ensure_user(db, telegram_id=ALEX_ID, username="alex", dm_chat_id=ALEX_ID)

    assert updated.dm_chat_id == ALEX_ID
    stored = get_user(db, ALEX_ID)
    assert stored is not None and stored.dm_chat_id == ALEX_ID


def test_shorts_stay_unique(db: Database) -> None:
    ensure_user(db, telegram_id=ALEX_ID, username="alex")
    twin = ensure_user(db, telegram_id=SASHA_ID, username="alex")

    assert twin.short != "alex"
    assert known_shorts(db) == frozenset({"alex", twin.short})


def test_a_user_without_a_username_still_gets_a_short(db: Database) -> None:
    user = ensure_user(db, telegram_id=SASHA_ID, first_name="Sasha")

    assert user.short == "sasha"
    assert user.display_name == "Sasha"


def test_migrations_are_applied_once(db: Database) -> None:
    assert db.schema_version() == 1
    assert db.migrate() == []


def test_the_schema_has_no_priority_column(db: Database) -> None:
    """Invariant: no priority field anywhere, in any form."""
    columns = {row["name"] for row in db.query("PRAGMA table_info(tasks)")}

    assert not any("priorit" in name.lower() for name in columns)
    assert "owner_id" in columns
