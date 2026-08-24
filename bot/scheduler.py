"""The two jobs that make the bot speak first (section 11).

A tick every 60 seconds does the four checks and then posts whatever the outbox
says is due; one cron job per hour sends the digest to whoever asked for that
hour. No persistent job store: every piece of state is a row in SQLite, so a
restart loses nothing and repeats nothing.

Nothing here sends a notification directly. Everything is queued through
`services.outbox`, which is what makes the quiet-hour rule impossible to skip.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot import render
from bot.config import Config
from bot.db import Database, to_iso
from bot.services import outbox
from bot.services.digest import build_digest
from bot.services.settings import LAST_TICK, group_chat_id, set_setting
from bot.services.tasks import OPEN_STATUSES, Task, is_blocked, row_to_task
from bot.services.users import User, get_user, list_users, partner_of

logger = logging.getLogger(__name__)

#: How often the tick runs.
TICK_SECONDS = 60

#: Individual overdue messages stop after this many days; the digest carries it after that.
OVERDUE_DAYS_SHOWN = 3

#: How many days late a task must be before the group hears about it.
ESCALATION_DAYS = 3


def build_scheduler(bot: Bot, db: Database, config: Config) -> AsyncIOScheduler:
    """One tick, one hourly digest check. Both jobs are cheap and idempotent."""
    scheduler = AsyncIOScheduler(timezone=config.tz)
    scheduler.add_job(
        tick,
        IntervalTrigger(seconds=TICK_SECONDS),
        args=(bot, db, config),
        id="tick",
        max_instances=1,
        coalesce=True,
        # Run once immediately: an interval trigger otherwise waits a whole
        # minute, which leaves anything the outbox owes from before the restart
        # sitting there, and makes a freshly started bot look dead to /health.
        next_run_time=datetime.now(config.tz),
    )
    scheduler.add_job(
        digest_round,
        CronTrigger(minute=0, timezone=config.tz),
        args=(bot, db, config),
        id="digest",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def tick(bot: Bot, db: Database, config: Config) -> None:
    """The four checks of section 11, then post whatever has come due."""
    now = datetime.now(UTC)
    try:
        plan_notifications(db, now=now, tz=config.tz)
    except Exception:  # pragma: no cover - a bad row must not kill the loop
        logger.exception("planning notifications failed")
    await flush_outbox(bot, db, now=now)
    # A heartbeat, so /health can say whether this loop is actually running.
    set_setting(db, LAST_TICK, to_iso(now) or "")


def plan_notifications(db: Database, *, now: datetime, tz: ZoneInfo) -> int:
    """Queue every notification that has become due. Returns how many were queued."""
    queued = 0
    for task in _open_dated_tasks(db):
        owner = get_user(db, task.owner_id)
        if owner is None:  # pragma: no cover - owner_id is a foreign key
            continue
        queued += _plan_reminder(db, task, owner, now=now, tz=tz)
        queued += _plan_overdue(db, task, owner, now=now, tz=tz)
        queued += _plan_follow_up(db, task, owner, now=now, tz=tz)
        queued += _plan_escalation(db, task, owner, now=now, tz=tz)
    return queued


def _open_dated_tasks(db: Database) -> list[Task]:
    rows = db.query(
        f"SELECT * FROM tasks WHERE status IN ({', '.join('?' for _ in OPEN_STATUSES)})",
        OPEN_STATUSES,
    )
    return [row_to_task(row) for row in rows]


def _plan_reminder(db: Database, task: Task, owner: User, *, now: datetime, tz: ZoneInfo) -> int:
    if task.status != "todo" or task.remind_at is None or task.remind_at > now:
        return 0
    if is_blocked(db, task.id):
        return 0  # blocked tasks never generate reminders (section 10)
    return _queue_dm(
        db,
        task,
        owner,
        kind="remind",
        text=render.reminder(task, now=now, tz=tz),
        now=now,
        tz=tz,
        keyboard=_card_keyboard(db, task),
        # Keyed on the reminder itself, not on today: otherwise a task nobody
        # closes would ping every morning for the rest of its life. Moving the
        # date sets a new key, so a rescheduled task reminds again.
        day=f"{task.remind_at.astimezone(tz):%Y-%m-%d}",
    )


def _plan_overdue(db: Database, task: Task, owner: User, *, now: datetime, tz: ZoneInfo) -> int:
    if task.due_at is None or task.due_at >= now:
        return 0
    days_late = (now - task.due_at).days
    if days_late < 1:
        # The reminder already fired at the due moment (remind_at defaults to
        # due_at); saying "late" a minute later would just be noise.
        return 0
    if days_late > OVERDUE_DAYS_SHOWN:
        return 0  # after three days it only appears in the digest
    if is_blocked(db, task.id):
        return 0
    return _queue_dm(
        db,
        task,
        owner,
        kind="overdue",
        text=render.overdue_ping(task, days_late=days_late, now=now, tz=tz),
        now=now,
        tz=tz,
        keyboard=_card_keyboard(db, task),
    )


def _plan_follow_up(db: Database, task: Task, owner: User, *, now: datetime, tz: ZoneInfo) -> int:
    if task.status != "waiting" or task.follow_up_at is None or task.follow_up_at > now:
        return 0
    return _queue_dm(
        db,
        task,
        owner,
        kind="followup",
        text=render.follow_up(task, now=now, tz=tz),
        now=now,
        tz=tz,
        keyboard=render.follow_up_keyboard(task),
    )


def _plan_escalation(db: Database, task: Task, owner: User, *, now: datetime, tz: ZoneInfo) -> int:
    """Only for people who asked for it, once per task, and never inside quiet hours."""
    if not owner.escalation or task.due_at is None:
        return 0
    if (now - task.due_at) < timedelta(days=ESCALATION_DAYS):
        return 0
    chat_id = group_chat_id(db)
    if chat_id is None:
        return 0
    day = f"{task.due_at.astimezone(tz):%Y-%m-%d}"  # once per task, keyed on its due date
    if not outbox.claim_saying(db, task_id=task.id, kind="escalation", day=day):
        return 0
    outbox.queue(
        db,
        chat_id=chat_id,
        text=render.escalation(task, owner, now=now, tz=tz),
        send_after=outbox.release_at(now, owner, tz=tz),
    )
    return 1


def _queue_dm(
    db: Database,
    task: Task,
    owner: User,
    *,
    kind: str,
    text: str,
    now: datetime,
    tz: ZoneInfo,
    keyboard: InlineKeyboardMarkup | None = None,
    day: str | None = None,
) -> int:
    day = day or outbox.local_day(now, tz)
    if not outbox.claim_saying(db, task_id=task.id, kind=kind, day=day):
        return 0
    queued = outbox.deliver_to(
        db,
        owner,
        text=text,
        now=now,
        tz=tz,
        keyboard=None if keyboard is None else keyboard.model_dump_json(),
    )
    if queued is None:
        logger.warning(
            "no private chat with %s yet, dropping %s for #%s", owner.short, kind, task.id
        )
        # Give the claim back: they may open a private chat before the next tick.
        db.execute(
            "DELETE FROM notifications_sent WHERE task_id = ? AND kind = ? AND day = ?",
            (task.id, kind, day),
        )
        return 0
    return 1


def _card_keyboard(db: Database, task: Task) -> InlineKeyboardMarkup | None:
    """A ping you cannot act on makes you go and find the task. Give it the buttons."""
    return render.task_keyboard(task, partner=partner_of(db, task.owner_id))


async def flush_outbox(bot: Bot, db: Database, *, now: datetime) -> int:
    """Post everything the queue says may go out now. Returns how many were sent."""
    sent = 0
    for message in outbox.due(db, now):
        markup = (
            InlineKeyboardMarkup.model_validate_json(message.keyboard) if message.keyboard else None
        )
        if not outbox.claim(db, message.id, now=now):
            continue  # another flush got there first
        try:
            await bot.send_message(message.chat_id, message.text, reply_markup=markup)
        except TelegramAPIError:
            logger.exception("could not deliver outbox message %s", message.id)
            outbox.release(db, message.id)
            continue
        sent += 1
    return sent


async def digest_round(bot: Bot, db: Database, config: Config) -> None:
    """Once an hour: whoever asked for this hour gets their morning, if it has anything in it."""
    now = datetime.now(UTC)
    try:
        queue_digests(db, now=now, tz=config.tz)
    except Exception:  # pragma: no cover - a bad row must not kill the loop
        logger.exception("building digests failed")
    await flush_outbox(bot, db, now=now)


def queue_digests(db: Database, *, now: datetime, tz: ZoneInfo) -> int:
    """Queue a digest for every user whose hour this is. Empty digests are not sent."""
    hour = now.astimezone(tz).hour
    day = outbox.local_day(now, tz)
    queued = 0
    for user in list_users(db):
        if user.digest_hour != hour:
            continue
        # `notifications_sent` has no user column, so the person goes in the kind.
        kind = f"digest:{user.telegram_id}"
        if outbox.already_said(db, task_id=0, kind=kind, day=day):
            continue
        digest = build_digest(db, user, now=now, tz=tz)
        if digest.is_empty:
            continue
        if outbox.deliver_to(db, user, text=render.digest(digest, now=now, tz=tz), now=now, tz=tz):
            outbox.remember_said(db, task_id=0, kind=kind, day=day)
            queued += 1
    return queued
