"""The quiet-hour queue, and the guard against saying the same thing twice.

**Invariant 3 lives here.** Nothing in this bot sends a notification directly:
every one is written to `outbox` with the moment it may leave, and the tick job
posts whatever has come due. A send that would land inside its owner's quiet
window is stamped with the end of that window instead. Nothing overrides this —
not escalation, not overdue, not a digest.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database, from_iso, to_iso
from bot.parser import PARIS
from bot.services.users import User


@dataclass(frozen=True)
class Queued:
    """One message waiting to go out."""

    id: int
    chat_id: int
    text: str
    keyboard: str | None
    send_after: datetime


def parse_hhmm(value: str) -> time:
    """`'21:00'` as stored in `users.quiet_start`."""
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def in_quiet_hours(moment: datetime, user: User, *, tz: ZoneInfo = PARIS) -> bool:
    """Is this instant inside the owner's quiet window? Windows may cross midnight."""
    start = parse_hhmm(user.quiet_start)
    end = parse_hhmm(user.quiet_end)
    if start == end:
        return False  # an empty window means the person wants no quiet hours
    now = moment.astimezone(tz).time()
    if start < end:
        return start <= now < end
    return now >= start or now < end


def release_at(moment: datetime, user: User, *, tz: ZoneInfo = PARIS) -> datetime:
    """When this message may go out: now, or the end of the quiet window it lands in."""
    if not in_quiet_hours(moment, user, tz=tz):
        return moment
    local = moment.astimezone(tz)
    end = parse_hhmm(user.quiet_end)
    candidate = datetime.combine(local.date(), end, tzinfo=tz)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def queue(
    db: Database,
    *,
    chat_id: int,
    text: str,
    send_after: datetime,
    keyboard: str | None = None,
) -> int:
    return db.insert(
        "INSERT INTO outbox (chat_id, text, keyboard, send_after) VALUES (?, ?, ?, ?)",
        (chat_id, text, keyboard, to_iso(send_after)),
    )


def deliver_to(
    db: Database,
    user: User,
    *,
    text: str,
    now: datetime,
    tz: ZoneInfo = PARIS,
    keyboard: str | None = None,
) -> int | None:
    """Queue a direct message for one person, held back if they are asleep.

    Returns None when we have no private chat with them yet — they have never sent
    `/start` in a private chat, so there is nowhere to put it.
    """
    if user.dm_chat_id is None:
        return None
    return queue(
        db,
        chat_id=user.dm_chat_id,
        text=text,
        send_after=release_at(now, user, tz=tz),
        keyboard=keyboard,
    )


def due(db: Database, now: datetime) -> list[Queued]:
    """Everything whose moment has come and which has not gone out yet."""
    rows = db.query(
        "SELECT * FROM outbox WHERE sent_at IS NULL AND send_after <= ? ORDER BY send_after, id",
        (to_iso(now),),
    )
    return [_row_to_queued(row) for row in rows]


def pending(db: Database) -> list[Queued]:
    rows = db.query("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY send_after, id")
    return [_row_to_queued(row) for row in rows]


def mark_sent(db: Database, message_id: int, *, now: datetime) -> None:
    db.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (to_iso(now), message_id))


def _row_to_queued(row: sqlite3.Row) -> Queued:
    send_after = from_iso(str(row["send_after"]))
    if send_after is None:  # pragma: no cover - the column is NOT NULL
        raise ValueError(f"outbox row {row['id']} has no send_after")
    return Queued(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        text=str(row["text"]),
        keyboard=None if row["keyboard"] is None else str(row["keyboard"]),
        send_after=send_after,
    )


# --- saying it only once ---------------------------------------------------


def already_said(db: Database, *, task_id: int, kind: str, day: str) -> bool:
    """`notifications_sent` is checked before every send (section 5)."""
    row = db.query_one(
        "SELECT 1 FROM notifications_sent WHERE task_id = ? AND kind = ? AND day = ?",
        (task_id, kind, day),
    )
    return row is not None


def remember_said(db: Database, *, task_id: int, kind: str, day: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO notifications_sent (task_id, kind, day) VALUES (?, ?, ?)",
        (task_id, kind, day),
    )


def local_day(moment: datetime, tz: ZoneInfo = PARIS) -> str:
    """The Paris date, which is what `notifications_sent.day` holds."""
    return f"{moment.astimezone(tz):%Y-%m-%d}"
