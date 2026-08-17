"""Phase 4: waiting, dependency chains, and the announcement when one comes free."""

from __future__ import annotations

from datetime import timedelta

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import announce_unblocked, build_view
from bot.services import outbox
from bot.services.settings import bind_group
from bot.services.tasks import (
    Task,
    add_dependency,
    blocked_map,
    blockers_of,
    complete_task,
    create_task,
    dependencies_of,
    drop_task,
    is_blocked,
    newly_unblocked,
    start_waiting,
)
from bot.services.users import User
from tests.conftest import ALEX_ID, NOW, SASHA_ID

GROUP_CHAT_ID = -100_555


def _task(db: Database, title: str, *, owner: int = ALEX_ID, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=owner,
        created_by=ALEX_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


# --- building a chain ------------------------------------------------------


def test_a_task_can_wait_for_another(db: Database, alex: User) -> None:
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")

    refused = add_dependency(db, mairie.id, docs.id)

    assert refused is None
    assert is_blocked(db, mairie.id)
    assert not is_blocked(db, docs.id)
    assert [blocker.id for blocker in blockers_of(db, mairie.id)] == [docs.id]
    assert [dep.id for dep in dependencies_of(db, mairie.id)] == [docs.id]


def test_the_same_dependency_twice_is_harmless(db: Database, alex: User) -> None:
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")

    assert add_dependency(db, mairie.id, docs.id) is None
    assert add_dependency(db, mairie.id, docs.id) is None
    assert len(blockers_of(db, mairie.id)) == 1


def test_a_task_cannot_wait_for_itself(db: Database, alex: User) -> None:
    task = _task(db, "go round in circles")

    refused = add_dependency(db, task.id, task.id)

    assert refused is not None
    assert "itself" in refused


def test_a_direct_loop_is_refused_and_explained(db: Database, alex: User) -> None:
    first = _task(db, "first")
    second = _task(db, "second")
    add_dependency(db, second.id, first.id)

    refused = add_dependency(db, first.id, second.id)

    assert refused is not None
    assert "loop" in refused
    assert blockers_of(db, first.id) == []


def test_a_loop_through_a_third_task_is_refused_too(db: Database, alex: User) -> None:
    first = _task(db, "first")
    second = _task(db, "second")
    third = _task(db, "third")
    add_dependency(db, second.id, first.id)
    add_dependency(db, third.id, second.id)

    refused = add_dependency(db, first.id, third.id)

    assert refused is not None
    assert blockers_of(db, first.id) == []


def test_blocking_on_a_task_that_does_not_exist_says_so(db: Database, alex: User) -> None:
    task = _task(db, "real")

    assert add_dependency(db, task.id, 4242) is not None
    assert add_dependency(db, 4242, task.id) is not None


# --- coming free -----------------------------------------------------------


def test_finishing_the_last_blocker_frees_the_task(db: Database, alex: User) -> None:
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)

    complete_task(db, docs.id, now=NOW)

    assert not is_blocked(db, mairie.id)
    assert [task.id for task in newly_unblocked(db, docs.id)] == [mairie.id]


def test_one_of_two_blockers_is_not_enough(db: Database, alex: User) -> None:
    docs = _task(db, "collect the payslips")
    money = _task(db, "pay the timbre fiscal")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)
    add_dependency(db, mairie.id, money.id)

    complete_task(db, docs.id, now=NOW)

    assert is_blocked(db, mairie.id)
    assert newly_unblocked(db, docs.id) == []


def test_a_dropped_blocker_also_frees_the_task(db: Database, alex: User) -> None:
    """Abandoning the thing in the way counts as clearing it."""
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)

    drop_task(db, docs.id)

    assert not is_blocked(db, mairie.id)


# --- announcing it ---------------------------------------------------------


def test_closing_a_blocker_posts_one_message_to_the_group(
    db: Database, alex: User, sasha: User, config: Config
) -> None:
    bind_group(db, GROUP_CHAT_ID)
    docs = _task(db, "collect the payslips")
    mairie = _task(
        db, "book the mairie appointment", owner=SASHA_ID, due_at=NOW + timedelta(days=2)
    )
    add_dependency(db, mairie.id, docs.id)
    done = complete_task(db, docs.id, now=NOW).task
    assert done is not None

    said = announce_unblocked(db, done, now=NOW, config=config)

    assert said == 1
    queued = [message for message in outbox.pending(db) if message.chat_id == GROUP_CHAT_ID]
    assert len(queued) == 1
    assert "free to start" in queued[0].text
    assert "sasha" in queued[0].text
    assert f"#{mairie.id}" in queued[0].text


def test_no_duplicate_announcement_on_restart(db: Database, alex: User, config: Config) -> None:
    """Phase 4 is done when a restart does not repeat the message."""
    bind_group(db, GROUP_CHAT_ID)
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)
    done = complete_task(db, docs.id, now=NOW).task
    assert done is not None

    announce_unblocked(db, done, now=NOW, config=config)
    announce_unblocked(db, done, now=NOW + timedelta(minutes=1), config=config)

    assert len(outbox.pending(db)) == 1


def test_nothing_is_announced_before_there_is_a_group(
    db: Database, alex: User, config: Config
) -> None:
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)
    done = complete_task(db, docs.id, now=NOW).task
    assert done is not None

    assert announce_unblocked(db, done, now=NOW, config=config) == 0


def test_the_announcement_waits_for_quiet_hours_to_end(
    db: Database, alex: User, config: Config
) -> None:
    from datetime import datetime

    from bot.parser import PARIS

    bind_group(db, GROUP_CHAT_ID)
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)
    at_eleven = datetime(2026, 9, 15, 23, 0, tzinfo=PARIS)
    done = complete_task(db, docs.id, now=at_eleven).task
    assert done is not None

    announce_unblocked(db, done, now=at_eleven, config=config)

    queued = outbox.pending(db)[0]
    assert queued.send_after.astimezone(PARIS) == datetime(2026, 9, 16, 7, 30, tzinfo=PARIS)


# --- how a blocked task looks ----------------------------------------------


def test_a_blocked_task_is_locked_and_last_in_a_list(
    db: Database, alex: User, config: Config
) -> None:
    free = _task(db, "pack the kitchen", due_at=NOW - timedelta(hours=1))
    docs = _task(db, "collect the payslips")
    blocked = _task(db, "book the mairie appointment", due_at=NOW - timedelta(hours=2))
    add_dependency(db, blocked.id, docs.id)

    text, _ = build_view("today", db, user=alex, now=NOW, config=config)

    assert "🔒" in text
    assert f"blocked by #{docs.id}" in text
    # the blocked one is later in the message than the free one, despite being due earlier
    assert text.index(f"#{free.id}") < text.index(f"#{blocked.id}")


def test_the_blocked_map_only_lists_what_is_really_blocked(db: Database, alex: User) -> None:
    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie")
    loose = _task(db, "buy milk")
    add_dependency(db, mairie.id, docs.id)

    mapping = blocked_map(db, [docs, mairie, loose])

    assert mapping == {mairie.id: [docs.id]}


def test_a_card_names_what_it_is_waiting_for(db: Database, alex: User, config: Config) -> None:
    from bot.handlers import card_text

    docs = _task(db, "collect the payslips")
    mairie = _task(db, "book the mairie appointment")
    add_dependency(db, mairie.id, docs.id)

    text = card_text(db, mairie, now=NOW, config=config)

    assert f"🔒 blocked by #{docs.id} collect the payslips" in text


# --- waiting ---------------------------------------------------------------


def test_wait_defaults_to_a_week_and_accepts_a_date(db: Database, alex: User) -> None:
    from datetime import datetime

    from bot.parser import PARIS

    task = _task(db, "wait for the mairie")

    parked = start_waiting(db, task.id, now=NOW)
    assert parked is not None and parked.follow_up_at is not None
    assert parked.follow_up_at.astimezone(PARIS) == datetime(2026, 9, 22, 10, 30, tzinfo=PARIS)

    chosen = datetime(2026, 10, 1, 9, 0, tzinfo=PARIS)
    with_date = start_waiting(db, task.id, now=NOW, follow_up_at=chosen)
    assert with_date is not None and with_date.follow_up_at == chosen


def test_the_unblock_message_names_the_date(db: Database, alex: User) -> None:
    task = _task(db, "book the mairie", due_at=NOW + timedelta(days=3))

    text = render.unblocked(task, alex, now=NOW)

    assert "free to start" in text
    assert "Fri 18 Sep" in text
