"""What happens once there are more tasks than fit on a screen.

Search, pagination, deletion, subtask progress, moving the group, and the weekly
review — the audit's backlog, and the edge cases each of them brings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import build_view
from bot.parser import DEFAULT_TZ
from bot.services import events, outbox
from bot.services.settings import GROUP_CHAT_ID, bind_group, group_chat_id
from bot.services.tasks import (
    Task,
    add_dependency,
    blocked_map,
    complete_task,
    create_task,
    delete_task,
    drop_task,
    get_task,
    search,
    subtask_progress,
)
from bot.services.users import User
from tests.conftest import NOW, ROBIN_ID, SAM_ID


def _task(db: Database, title: str, *, owner: int = ROBIN_ID, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=owner,
        created_by=ROBIN_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


# --- search ----------------------------------------------------------------


def test_search_looks_in_title_project_and_notes(db: Database, robin: User) -> None:
    from bot.services.tasks import append_note

    by_title = _task(db, "call the landlord")
    by_project = _task(db, "pack the kitchen", project="landlord-stuff")
    by_note = _task(db, "something else")
    append_note(db, by_note.id, text="the landlord wants a payslip", now=NOW)
    _task(db, "buy milk")

    found = {task.id for task in search(db, "landlord")}

    assert found == {by_title.id, by_project.id, by_note.id}


def test_search_is_case_insensitive_and_ignores_blanks(db: Database, robin: User) -> None:
    _task(db, "Call The Landlord")

    assert len(search(db, "LANDLORD")) == 1
    assert search(db, "   ") == []
    assert search(db, "") == []


def test_search_finds_closed_tasks_too_but_puts_them_last(db: Database, robin: User) -> None:
    """ "What did we call that thing we did?" is half of searching."""
    closed = _task(db, "landlord: the old one")
    complete_task(db, closed.id, now=NOW)
    open_one = _task(db, "landlord: the new one")

    found = search(db, "landlord")

    assert [task.id for task in found] == [open_one.id, closed.id]
    assert search(db, "landlord", include_closed=False) == [get_task(db, open_one.id)]


def test_the_search_view_separates_open_from_closed(
    db: Database, robin: User, config: Config
) -> None:
    closed = _task(db, "landlord old")
    complete_task(db, closed.id, now=NOW)
    _task(db, "landlord new")

    text, markup = build_view("find", db, user=robin, now=NOW, config=config, query="landlord")

    assert "🔍 <b>landlord</b> — 2 match(es)" in text
    assert "<b>Closed</b>" in text
    assert markup.inline_keyboard


def test_a_search_with_no_hits_says_so(db: Database, robin: User, config: Config) -> None:
    text, _ = build_view("find", db, user=robin, now=NOW, config=config, query="unicorn")

    assert "Nothing matches" in text


def test_a_search_escapes_what_was_typed(db: Database, robin: User, config: Config) -> None:
    text, _ = build_view("find", db, user=robin, now=NOW, config=config, query="<b>x</b>")

    assert "&lt;b&gt;" in text


# --- pagination ------------------------------------------------------------


def test_a_long_list_is_cut_into_pages(db: Database, robin: User, config: Config) -> None:
    for index in range(25):
        _task(db, f"task {index}", due_at=NOW - timedelta(hours=index + 1))

    first, markup = build_view("today", db, user=robin, now=NOW, config=config, page=0)
    payloads = [button.callback_data for row in markup.inline_keyboard for button in row]

    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert "1/3" in labels
    assert "25 shown" in first or "of 25 shown" in first
    assert "m:today:1" in payloads  # a next page exists
    assert "m:today:-1" not in payloads  # and no previous one


def test_the_last_page_has_no_next_arrow(db: Database, robin: User, config: Config) -> None:
    for index in range(25):
        _task(db, f"task {index}", due_at=NOW - timedelta(hours=index + 1))

    _, markup = build_view("today", db, user=robin, now=NOW, config=config, page=2)
    payloads = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "m:today:1" in payloads  # previous
    assert "m:today:3" not in payloads  # nothing after the end


def test_a_page_past_the_end_lands_on_the_last_one(
    db: Database, robin: User, config: Config
) -> None:
    """A stale button from an old message must not show an empty page."""
    for index in range(12):
        _task(db, f"task {index}", due_at=NOW - timedelta(hours=index + 1))

    text, markup = build_view("today", db, user=robin, now=NOW, config=config, page=99)

    assert "task" in text
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "2/2" in labels


def test_a_short_list_shows_no_arrows_at_all(db: Database, robin: User, config: Config) -> None:
    _task(db, "only one", due_at=NOW)

    _, markup = build_view("today", db, user=robin, now=NOW, config=config)
    labels = [b.text for row in markup.inline_keyboard for b in row]

    assert not any(label in {"◀", "▶"} for label in labels)


def test_page_of_handles_the_empty_case() -> None:
    shown, page, pages = render.page_of([], 3)

    assert (shown, page, pages) == ([], 0, 1)


# --- deleting for good -----------------------------------------------------


def test_delete_takes_the_subtasks_and_the_history(db: Database, robin: User) -> None:
    parent = _task(db, "the wrong task")
    child = _task(db, "its subtask", parent_id=parent.id)
    complete_task(db, child.id, now=NOW)

    removed = delete_task(db, parent.id)

    assert removed is not None and removed.title == "the wrong task"
    assert get_task(db, parent.id) is None
    assert get_task(db, child.id) is None
    assert (
        db.query("SELECT * FROM task_events WHERE task_id IN (?, ?)", (parent.id, child.id)) == []
    )


def test_delete_keeps_the_scans(db: Database, robin: User) -> None:
    """The scan outlives the task it was filed against."""
    from bot.services import docs as doc_service

    task = _task(db, "the wrong task")
    stored = doc_service.store(
        db,
        file_id="F",
        file_unique_id="U",
        kind="document",
        added_by=ROBIN_ID,
        added_at=NOW,
        task_id=task.id,
    )

    delete_task(db, task.id)

    kept = doc_service.get_attachment(db, stored.id)
    assert kept is not None and kept.task_id is None


def test_delete_clears_dependencies_both_ways(db: Database, robin: User) -> None:
    first = _task(db, "first")
    second = _task(db, "second")
    add_dependency(db, second.id, first.id)

    delete_task(db, first.id)

    assert db.query("SELECT * FROM task_deps") == []
    assert blocked_map(db, [get_task(db, second.id)]) == {}  # type: ignore[list-item]


def test_deleting_something_that_is_gone_is_harmless(db: Database) -> None:
    assert delete_task(db, 4242) is None


def test_a_closed_card_offers_delete_behind_a_confirmation(db: Database, robin: User) -> None:
    task = _task(db, "never mind")
    dropped = drop_task(db, task.id)
    assert dropped is not None

    labels = [b.text for row in render.task_keyboard(dropped).inline_keyboard for b in row]  # type: ignore[union-attr]
    confirm = render.confirm_delete_keyboard(dropped)
    confirm_payloads = [b.callback_data for row in confirm.inline_keyboard for b in row]

    assert labels == ["↩️ Reopen", "🗑 Delete"]
    assert confirm_payloads == [f"t:delete_yes:{task.id}", f"t:delete_no:{task.id}"]
    assert "cannot be undone" in render.CONFIRM_DELETE


# --- subtask progress ------------------------------------------------------


def test_a_card_shows_how_its_subtasks_are_going(db: Database, robin: User, config: Config) -> None:
    from bot.handlers import card_text

    parent = _task(db, "the move")
    first = _task(db, "step one", parent_id=parent.id)
    _task(db, "step two", parent_id=parent.id)
    complete_task(db, first.id, now=NOW)

    text = card_text(db, parent, now=NOW, config=config)

    assert "1 of 2 steps" in text
    assert subtask_progress(db, [parent.id]) == {parent.id: (1, 2)}


def test_a_task_with_no_subtasks_says_nothing_about_them(
    db: Database, robin: User, config: Config
) -> None:
    from bot.handlers import card_text

    task = _task(db, "alone")

    assert "subtasks" not in card_text(db, task, now=NOW, config=config)


# --- one query, not one per row --------------------------------------------


def test_the_blocked_map_is_one_query_for_the_whole_list(db: Database, robin: User) -> None:
    blocker = _task(db, "the blocker")
    blocked = [_task(db, f"waiting {index}") for index in range(5)]
    for task in blocked:
        add_dependency(db, task.id, blocker.id)
    everything = [blocker, *blocked]

    mapping = blocked_map(db, everything)

    assert set(mapping) == {task.id for task in blocked}
    assert all(ids == [blocker.id] for ids in mapping.values())


def test_a_finished_blocker_leaves_nothing_blocked(db: Database, robin: User) -> None:
    blocker = _task(db, "the blocker")
    blocked = _task(db, "waiting")
    add_dependency(db, blocked.id, blocker.id)
    complete_task(db, blocker.id, now=NOW)

    from bot.services.tasks import blocked_ids

    assert blocked_map(db, [blocked]) == {}
    assert blocked_ids(db) == set()


# --- moving the bot to another group ---------------------------------------


def test_the_group_can_be_moved(db: Database) -> None:
    from bot.services.settings import set_int

    bind_group(db, -100_111)
    set_int(db, GROUP_CHAT_ID, -100_222)

    assert group_chat_id(db) == -100_222


def test_the_claim_command_gets_past_the_group_gate() -> None:
    """Otherwise /group could never be heard in the group it is meant to claim."""
    from datetime import UTC

    from aiogram.types import Chat, Message, Update
    from aiogram.types import User as TelegramUser

    from bot.middleware.whitelist import _is_claim_command

    def update(text: str) -> Update:
        return Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime(2026, 9, 15, 8, 30, tzinfo=UTC),
                chat=Chat(id=-100_999, type="group"),
                from_user=TelegramUser(id=ROBIN_ID, is_bot=False, first_name="Robin"),
                text=text,
            ),
        )

    assert _is_claim_command(update("/group"))
    assert _is_claim_command(update("/group@mybot"))
    assert not _is_claim_command(update("/today"))
    assert not _is_claim_command(update("just talking"))


# --- the weekly review -----------------------------------------------------


def test_the_week_is_reviewed_on_sunday_only(db: Database) -> None:
    from bot.scheduler import queue_weekly_review
    from bot.services.users import ensure_user

    ensure_user(db, telegram_id=ROBIN_ID, username="robin", dm_chat_id=ROBIN_ID)
    db.execute("UPDATE users SET digest_hour = 18 WHERE telegram_id = ?", (ROBIN_ID,))
    task = _task(db, "something")
    events.record(db, task_id=task.id, kind=events.DONE, at=NOW)

    saturday = datetime(2026, 9, 19, 18, 0, tzinfo=DEFAULT_TZ)
    sunday = datetime(2026, 9, 20, 18, 0, tzinfo=DEFAULT_TZ)
    wrong_hour = datetime(2026, 9, 20, 9, 0, tzinfo=DEFAULT_TZ)

    assert queue_weekly_review(db, now=saturday, tz=DEFAULT_TZ) == 0
    assert queue_weekly_review(db, now=wrong_hour, tz=DEFAULT_TZ) == 0
    assert queue_weekly_review(db, now=sunday, tz=DEFAULT_TZ) == 1
    assert queue_weekly_review(db, now=sunday, tz=DEFAULT_TZ) == 0  # once a week

    assert any("Your week" in message.text for message in outbox.pending(db))


def test_the_weekly_review_waits_for_quiet_hours(db: Database) -> None:
    from bot.scheduler import queue_weekly_review
    from bot.services.users import ensure_user

    ensure_user(db, telegram_id=ROBIN_ID, username="robin", dm_chat_id=ROBIN_ID)
    db.execute("UPDATE users SET digest_hour = 22 WHERE telegram_id = ?", (ROBIN_ID,))
    task = _task(db, "something")
    events.record(db, task_id=task.id, kind=events.DONE, at=NOW)

    late_sunday = datetime(2026, 9, 20, 22, 0, tzinfo=DEFAULT_TZ)
    assert queue_weekly_review(db, now=late_sunday, tz=DEFAULT_TZ) == 1

    queued = outbox.pending(db)[0]
    assert queued.send_after.astimezone(DEFAULT_TZ) == datetime(
        2026, 9, 21, 7, 30, tzinfo=DEFAULT_TZ
    )


def test_a_silent_week_is_not_reviewed(db: Database) -> None:
    from bot.scheduler import queue_weekly_review
    from bot.services.users import ensure_user

    ensure_user(db, telegram_id=ROBIN_ID, username="robin", dm_chat_id=ROBIN_ID)
    db.execute("UPDATE users SET digest_hour = 18 WHERE telegram_id = ?", (ROBIN_ID,))

    sunday = datetime(2026, 9, 20, 18, 0, tzinfo=DEFAULT_TZ)

    assert queue_weekly_review(db, now=sunday, tz=DEFAULT_TZ) == 0
    assert outbox.pending(db) == []


def test_both_people_get_their_own_review(db: Database) -> None:
    from bot.scheduler import queue_weekly_review
    from bot.services.users import ensure_user

    ensure_user(db, telegram_id=ROBIN_ID, username="robin", dm_chat_id=ROBIN_ID)
    ensure_user(db, telegram_id=SAM_ID, username="sam", dm_chat_id=SAM_ID)
    db.execute("UPDATE users SET digest_hour = 18")
    task = _task(db, "something")
    events.record(db, task_id=task.id, kind=events.DONE, at=NOW)

    sunday = datetime(2026, 9, 20, 18, 0, tzinfo=DEFAULT_TZ)

    assert queue_weekly_review(db, now=sunday, tz=DEFAULT_TZ) == 2
    assert {m.chat_id for m in outbox.pending(db)} == {ROBIN_ID, SAM_ID}
