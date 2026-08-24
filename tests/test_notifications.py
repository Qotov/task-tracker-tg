"""Phase 3: reminders, the digest, and the quiet hours that outrank both.

The two tests section 11 asks for by name are here: a reminder at 23:00 is
delivered at 07:30, and the reminder logic survives a daylight saving change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from bot import render
from bot.db import Database
from bot.parser import DEFAULT_TZ
from bot.scheduler import plan_notifications, queue_digests
from bot.services import outbox
from bot.services.digest import build_digest
from bot.services.settings import bind_group, group_chat_id
from bot.services.tasks import Task, complete_task, create_task, start_waiting
from bot.services.users import User, adjust_setting, ensure_user, get_user
from tests.conftest import NOW, ROBIN_ID, SAM_ID

GROUP_CHAT_ID = -100_555


def _task(db: Database, title: str, *, owner: int = ROBIN_ID, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=owner,
        created_by=ROBIN_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def _with_dm(db: Database, telegram_id: int = ROBIN_ID) -> User:
    """A user we can actually send a direct message to."""
    return ensure_user(db, telegram_id=telegram_id, username="robin", dm_chat_id=telegram_id)


# --- the quiet window ------------------------------------------------------


def test_the_default_window_wraps_around_midnight(db: Database, robin: User) -> None:
    late = datetime(2026, 9, 15, 23, 0, tzinfo=DEFAULT_TZ)
    early = datetime(2026, 9, 16, 3, 0, tzinfo=DEFAULT_TZ)
    daytime = datetime(2026, 9, 15, 12, 0, tzinfo=DEFAULT_TZ)

    assert outbox.in_quiet_hours(late, robin)
    assert outbox.in_quiet_hours(early, robin)
    assert not outbox.in_quiet_hours(daytime, robin)


def test_a_window_that_does_not_wrap_still_works(db: Database, robin: User) -> None:
    napper = ensure_user(db, telegram_id=SAM_ID, username="sam")
    db.execute(
        "UPDATE users SET quiet_start = '13:00', quiet_end = '15:00' WHERE telegram_id = ?",
        (SAM_ID,),
    )
    napper = get_user(db, SAM_ID) or napper

    assert outbox.in_quiet_hours(datetime(2026, 9, 15, 14, 0, tzinfo=DEFAULT_TZ), napper)
    assert not outbox.in_quiet_hours(datetime(2026, 9, 15, 23, 0, tzinfo=DEFAULT_TZ), napper)


def test_an_empty_window_means_no_quiet_hours(db: Database) -> None:
    always_on = ensure_user(db, telegram_id=SAM_ID, username="sam")
    db.execute(
        "UPDATE users SET quiet_start = '00:00', quiet_end = '00:00' WHERE telegram_id = ?",
        (SAM_ID,),
    )
    always_on = get_user(db, SAM_ID) or always_on

    assert not outbox.in_quiet_hours(datetime(2026, 9, 15, 3, 0, tzinfo=DEFAULT_TZ), always_on)


def test_the_release_time_is_the_end_of_the_window(db: Database, robin: User) -> None:
    at_eleven = datetime(2026, 9, 15, 23, 0, tzinfo=DEFAULT_TZ)

    released = outbox.release_at(at_eleven, robin)

    assert released.astimezone(DEFAULT_TZ) == datetime(2026, 9, 16, 7, 30, tzinfo=DEFAULT_TZ)


def test_something_sent_in_daylight_goes_out_at_once(db: Database, robin: User) -> None:
    midday = datetime(2026, 9, 15, 12, 0, tzinfo=DEFAULT_TZ)

    assert outbox.release_at(midday, robin) == midday


# --- the test section 11 asks for by name ----------------------------------


def test_a_reminder_at_23_00_is_delivered_at_07_30(db: Database) -> None:
    """Invariant 3, end to end: nothing leaves the bot inside the quiet window."""
    _with_dm(db)
    at_eleven = datetime(2026, 9, 15, 23, 0, tzinfo=DEFAULT_TZ)
    _task(db, "call the mairie", due_at=at_eleven, remind_at=at_eleven)

    queued = plan_notifications(db, now=at_eleven, tz=DEFAULT_TZ)

    assert queued == 1
    waiting = outbox.pending(db)
    assert len(waiting) == 1
    assert waiting[0].send_after.astimezone(DEFAULT_TZ) == datetime(
        2026, 9, 16, 7, 30, tzinfo=DEFAULT_TZ
    )
    assert outbox.due(db, at_eleven) == []
    assert outbox.due(db, datetime(2026, 9, 16, 7, 29, tzinfo=DEFAULT_TZ)) == []
    assert len(outbox.due(db, datetime(2026, 9, 16, 7, 30, tzinfo=DEFAULT_TZ))) == 1


def test_the_reminder_hour_holds_across_the_daylight_saving_change(db: Database) -> None:
    """Paris drops to UTC+1 on 25 October 2026; a 09:00 reminder is still 09:00."""
    _with_dm(db)
    before = datetime(2026, 10, 24, 9, 0, tzinfo=DEFAULT_TZ)
    after = datetime(2026, 10, 26, 9, 0, tzinfo=DEFAULT_TZ)
    # remind_at only, so the overdue check stays out of the count
    _task(db, "before the change", remind_at=before)
    _task(db, "after the change", remind_at=after)

    assert plan_notifications(db, now=before, tz=DEFAULT_TZ) == 1
    assert plan_notifications(db, now=after, tz=DEFAULT_TZ) == 1

    sent = outbox.pending(db)
    assert [message.send_after for message in sent] == [before, after]
    assert before.utcoffset() != after.utcoffset()  # the offsets really did differ
    assert [m.send_after.astimezone(DEFAULT_TZ).hour for m in sent] == [9, 9]


# --- the four checks -------------------------------------------------------


def test_a_reminder_fires_once_and_not_again(db: Database) -> None:
    """Keyed on `remind_at`, so an untouched task does not ping every morning."""
    _with_dm(db)
    _task(db, "call the mairie", remind_at=NOW)

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 1
    assert plan_notifications(db, now=NOW + timedelta(minutes=1), tz=DEFAULT_TZ) == 0
    assert plan_notifications(db, now=NOW + timedelta(days=1), tz=DEFAULT_TZ) == 0


def test_the_due_moment_does_not_ping_twice(db: Database) -> None:
    """A reminder at the due time, then "you are late" a minute later, is noise."""
    _with_dm(db)
    _task(db, "call the mairie", due_at=NOW, remind_at=NOW)

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 1
    assert plan_notifications(db, now=NOW + timedelta(minutes=1), tz=DEFAULT_TZ) == 0
    assert plan_notifications(db, now=NOW + timedelta(days=1, minutes=1), tz=DEFAULT_TZ) == 1


def test_nothing_is_reminded_before_its_time(db: Database) -> None:
    _with_dm(db)
    later = NOW + timedelta(hours=2)
    _task(db, "later today", due_at=later, remind_at=later)

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 0


def test_a_blocked_task_never_pings(db: Database) -> None:
    """Section 10: blocked tasks generate no reminders."""
    _with_dm(db)
    blocker = _task(db, "collect the payslips", remind_at=NOW)
    blocked = _task(db, "book the mairie", remind_at=NOW)
    db.execute(
        "INSERT INTO task_deps (task_id, depends_on_id) VALUES (?, ?)", (blocked.id, blocker.id)
    )

    planned = plan_notifications(db, now=NOW, tz=DEFAULT_TZ)

    assert planned == 1  # only the blocker itself
    assert all(f"#{blocked.id}" not in message.text for message in outbox.pending(db))


def test_overdue_stops_after_three_days(db: Database) -> None:
    _with_dm(db)
    _task(db, "long forgotten", due_at=NOW - timedelta(days=5))

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 0
    assert outbox.pending(db) == []


def test_a_task_two_days_late_still_pings(db: Database) -> None:
    _with_dm(db)
    _task(db, "the deposit", due_at=NOW - timedelta(days=2))

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) >= 1
    assert any("late" in message.text for message in outbox.pending(db))


def test_a_waiting_task_asks_whether_there_was_an_answer(db: Database) -> None:
    _with_dm(db)
    task = _task(db, "wait for the mairie")
    start_waiting(db, task.id, now=NOW - timedelta(days=8))

    plan_notifications(db, now=NOW, tz=DEFAULT_TZ)

    pings = [message for message in outbox.pending(db) if "No answer yet" in message.text]
    assert len(pings) == 1
    assert pings[0].keyboard is not None  # extend by a week, or mark it done


def test_escalation_only_happens_when_it_was_asked_for(db: Database) -> None:
    robin = _with_dm(db)
    bind_group(db, GROUP_CHAT_ID)
    due = NOW - timedelta(days=4)
    _task(db, "the landlord", due_at=due)

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 0  # escalation is off by default

    adjust_setting(db, robin, field="escalation", step=1)
    plan_notifications(db, now=NOW, tz=DEFAULT_TZ)

    to_group = [m for m in outbox.pending(db) if m.chat_id == GROUP_CHAT_ID]
    assert len(to_group) == 1
    assert "three days" in to_group[0].text


def test_the_group_is_only_told_once(db: Database) -> None:
    robin = _with_dm(db)
    bind_group(db, GROUP_CHAT_ID)
    adjust_setting(db, robin, field="escalation", step=1)
    due = NOW - timedelta(days=4)
    _task(db, "the landlord", due_at=due)

    plan_notifications(db, now=NOW, tz=DEFAULT_TZ)
    plan_notifications(db, now=NOW + timedelta(days=1), tz=DEFAULT_TZ)

    assert len([m for m in outbox.pending(db) if m.chat_id == GROUP_CHAT_ID]) == 1


def test_nothing_is_queued_for_someone_we_cannot_message(db: Database, robin: User) -> None:
    """`robin` here has never sent /start in a private chat."""
    _task(db, "call the mairie", due_at=NOW, remind_at=NOW)

    assert plan_notifications(db, now=NOW, tz=DEFAULT_TZ) == 0
    assert outbox.pending(db) == []


# --- the digest ------------------------------------------------------------


def test_the_digest_has_all_four_sections(db: Database) -> None:
    robin = _with_dm(db)
    _task(db, "due later today", due_at=NOW + timedelta(hours=4))
    _task(db, "late already", due_at=NOW - timedelta(days=1))
    stalled = _task(db, "waiting on them")
    start_waiting(db, stalled.id, now=NOW - timedelta(days=7))
    blocker = _task(db, "the blocker")
    freed = _task(db, "now free")
    db.execute(
        "INSERT INTO task_deps (task_id, depends_on_id) VALUES (?, ?)", (freed.id, blocker.id)
    )
    complete_task(db, blocker.id, now=NOW - timedelta(hours=2))

    digest = build_digest(db, robin, now=NOW, tz=DEFAULT_TZ)

    assert [task.title for task in digest.due_today] == ["due later today"]
    assert [task.title for task in digest.overdue] == ["late already"]
    assert [task.title for task in digest.unblocked] == ["now free"]
    assert [task.title for task in digest.follow_ups] == ["waiting on them"]
    assert not digest.is_empty

    text = render.digest(digest, now=NOW, tz=DEFAULT_TZ)
    assert "Due today" in text and "Late" in text and "Came free" in text


def test_an_empty_digest_is_never_sent(db: Database) -> None:
    robin = _with_dm(db)
    db.execute("UPDATE users SET digest_hour = ? WHERE telegram_id = ?", (10, ROBIN_ID))

    assert build_digest(db, robin, now=NOW, tz=DEFAULT_TZ).is_empty
    assert queue_digests(db, now=NOW, tz=DEFAULT_TZ) == 0
    assert outbox.pending(db) == []


def test_the_digest_goes_out_at_the_hour_that_was_asked_for(db: Database) -> None:
    _with_dm(db)
    db.execute("UPDATE users SET digest_hour = ? WHERE telegram_id = ?", (10, ROBIN_ID))
    _task(db, "due later today", due_at=NOW + timedelta(hours=4))

    assert queue_digests(db, now=NOW.replace(hour=9), tz=DEFAULT_TZ) == 0  # NOW is 10:30 Paris
    assert queue_digests(db, now=NOW, tz=DEFAULT_TZ) == 1
    assert queue_digests(db, now=NOW + timedelta(minutes=5), tz=DEFAULT_TZ) == 0  # once a day


def test_a_digest_inside_quiet_hours_waits_too(db: Database) -> None:
    """Invariant 3 has no exception for the digest."""
    _with_dm(db)
    db.execute("UPDATE users SET digest_hour = ? WHERE telegram_id = ?", (6, ROBIN_ID))
    early = datetime(2026, 9, 16, 6, 0, tzinfo=DEFAULT_TZ)
    _task(db, "due later today", due_at=early + timedelta(hours=6))

    assert queue_digests(db, now=early, tz=DEFAULT_TZ) == 1

    waiting = outbox.pending(db)
    assert waiting[0].send_after.astimezone(DEFAULT_TZ) == datetime(
        2026, 9, 16, 7, 30, tzinfo=DEFAULT_TZ
    )


# --- settings --------------------------------------------------------------


def test_settings_can_be_nudged_by_button(db: Database, robin: User) -> None:
    later = adjust_setting(db, robin, field="digest", step=1)
    assert later.digest_hour == 9

    quieter = adjust_setting(db, later, field="quiet_start", step=-1)
    assert quieter.quiet_start == "20:30"

    louder = adjust_setting(db, quieter, field="quiet_end", step=1)
    assert louder.quiet_end == "08:00"

    on = adjust_setting(db, louder, field="escalation", step=1)
    assert on.escalation is True
    assert adjust_setting(db, on, field="escalation", step=1).escalation is False


def test_the_digest_hour_wraps_round_the_clock(db: Database, robin: User) -> None:
    db.execute("UPDATE users SET digest_hour = 23 WHERE telegram_id = ?", (ROBIN_ID,))
    late = get_user(db, ROBIN_ID)
    assert late is not None

    assert adjust_setting(db, late, field="digest", step=1).digest_hour == 0


def test_quiet_hours_wrap_round_midnight_too(db: Database, robin: User) -> None:
    db.execute("UPDATE users SET quiet_start = '00:15' WHERE telegram_id = ?", (ROBIN_ID,))
    user = get_user(db, ROBIN_ID)
    assert user is not None

    assert adjust_setting(db, user, field="quiet_start", step=-1).quiet_start == "23:45"


def test_the_settings_card_names_every_value(db: Database, robin: User) -> None:
    text = render.settings_text(robin)

    assert "08:00" in text and "21:00" in text and "07:30" in text and "off" in text


# --- the group -------------------------------------------------------------


def test_the_first_group_wins(db: Database) -> None:
    assert bind_group(db, GROUP_CHAT_ID) is True
    assert bind_group(db, GROUP_CHAT_ID) is True
    assert bind_group(db, -100_999) is False
    assert group_chat_id(db) == GROUP_CHAT_ID


# --- the queue itself ------------------------------------------------------


def test_a_sent_message_is_not_sent_again(db: Database, robin: User) -> None:
    with freeze_time("2026-09-15T08:30:00+00:00"):
        message_id = outbox.queue(db, chat_id=1, text="hello", send_after=datetime.now(UTC))
        assert len(outbox.due(db, datetime.now(UTC))) == 1

        outbox.mark_sent(db, message_id, now=datetime.now(UTC))

        assert outbox.due(db, datetime.now(UTC)) == []
        assert outbox.pending(db) == []


# --- is it working? --------------------------------------------------------


def test_health_reports_a_stalled_scheduler(db: Database, robin: User) -> None:
    from bot.services.health import check

    report = check(db, now=NOW)

    assert report.last_tick is None
    assert not report.ticking
    assert "never ticked" in render.health(report)


def test_health_reports_a_live_scheduler_and_what_is_held(db: Database) -> None:
    from bot.services.health import check
    from bot.services.settings import LAST_TICK, set_setting

    robin = _with_dm(db)
    set_setting(db, LAST_TICK, "2026-09-15T08:29:30+00:00")  # 30 seconds before NOW
    at_eleven = datetime(2026, 9, 15, 23, 0, tzinfo=DEFAULT_TZ)
    _task(db, "call the mairie", remind_at=at_eleven)
    plan_notifications(db, now=at_eleven, tz=DEFAULT_TZ)

    report = check(db, now=NOW)
    text = render.health(report)

    assert report.ticking
    assert report.tick_age_seconds == 30
    assert "Scheduler alive" in text
    assert "1 message(s) held" in text
    assert report.unreachable == ()
    del robin


def test_health_names_anybody_the_bot_cannot_message(db: Database, robin: User) -> None:
    from bot.services.health import check

    report = check(db, now=NOW)

    assert [user.short for user in report.unreachable] == ["robin"]
    assert "must send /start" in render.health(report)


def test_health_names_the_clock_it_works_in(db: Database, robin: User) -> None:
    """A timezone mismatch is the easiest way to think a reminder is broken."""
    from bot.services.health import check

    text = render.health(check(db, now=NOW), tz=DEFAULT_TZ)

    assert "10:30" in text  # NOW in the display timezone
    assert "Europe/Paris" in text


def test_a_reminder_does_not_scold_you_for_the_ticks_own_delay(db: Database, robin: User) -> None:
    """The tick runs once a minute, so a reminder always arrives just after due."""
    task = _task(db, "test ping", due_at=NOW)

    text = render.reminder(task, now=NOW + timedelta(seconds=24), tz=DEFAULT_TZ)

    assert "🔔 <b>Reminder</b>" in text  # obviously the bot speaking first
    assert "⚠️" not in text
    assert "due now" in text  # not "24 seconds late"


def test_a_ping_can_be_acted_on_where_it_lands(db: Database) -> None:
    """A reminder you cannot close makes you go and find the task."""
    _with_dm(db)
    _task(db, "test ping", remind_at=NOW)

    plan_notifications(db, now=NOW, tz=DEFAULT_TZ)

    queued = outbox.pending(db)[0]
    assert queued.keyboard is not None
    assert "t:done:1" in queued.keyboard
    assert "🔔 <b>Reminder</b>" in queued.text


def test_an_overdue_ping_says_how_late_and_carries_buttons(db: Database) -> None:
    _with_dm(db)
    _task(db, "the deposit", due_at=NOW - timedelta(days=2))

    plan_notifications(db, now=NOW, tz=DEFAULT_TZ)

    queued = [message for message in outbox.pending(db) if "Still open" in message.text][0]
    assert "2 days late" in queued.text
    assert queued.keyboard is not None and "t:done:" in queued.keyboard


# --- doing a thing exactly once --------------------------------------------


def test_two_flushes_at_the_same_moment_send_one_message(db: Database) -> None:
    """The tick and the hourly digest collide at :00; only one may send."""
    import asyncio

    from bot.scheduler import flush_outbox

    class SlowBot:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_message(self, chat_id: int, text: str, reply_markup: object = None) -> None:
            await asyncio.sleep(0.02)  # the network round trip that opens the gap
            self.sent.append(text)

    outbox.queue(db, chat_id=1, text="⏰ your reminder", send_after=NOW)
    bot = SlowBot()

    async def both() -> None:
        await asyncio.gather(
            flush_outbox(bot, db, now=NOW),  # type: ignore[arg-type]
            flush_outbox(bot, db, now=NOW),  # type: ignore[arg-type]
        )

    asyncio.run(both())

    assert len(bot.sent) == 1
    assert outbox.pending(db) == []


def test_a_failed_send_is_handed_back_for_the_next_tick(db: Database) -> None:
    import asyncio

    from aiogram.exceptions import TelegramAPIError

    from bot.scheduler import flush_outbox

    class BrokenBot:
        async def send_message(self, chat_id: int, text: str, reply_markup: object = None) -> None:
            raise TelegramAPIError(method=None, message="nope")  # type: ignore[arg-type]

    outbox.queue(db, chat_id=1, text="⏰ your reminder", send_after=NOW)

    asyncio.run(flush_outbox(BrokenBot(), db, now=NOW))  # type: ignore[arg-type]

    assert len(outbox.pending(db)) == 1  # still queued, will be retried


def test_only_one_caller_may_say_a_thing(db: Database) -> None:
    assert outbox.claim_saying(db, task_id=1, kind="remind", day="2026-09-15") is True
    assert outbox.claim_saying(db, task_id=1, kind="remind", day="2026-09-15") is False


def test_completing_twice_rolls_one_next_instance(db: Database, robin: User) -> None:
    """A double tap must not produce two copies of a repeating task."""
    from bot.services.recurrence import Recurrence
    from bot.services.tasks import complete_task, set_recurrence

    task = _task(db, "take the bins out", due_at=NOW)
    set_recurrence(db, task.id, rule=Recurrence("daily"))

    first = complete_task(db, task.id, now=NOW)
    second = complete_task(db, task.id, now=NOW)

    assert first.next_instance is not None
    assert second.already_done is True
    assert second.next_instance is None
    assert len(db.query("SELECT * FROM tasks")) == 2  # the original and exactly one copy
