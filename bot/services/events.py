"""What happened, and when — the history the `tasks` table cannot keep.

`tasks` holds the present: one status, one `done_at` that is wiped the moment
somebody reopens something. Every honest answer to "am I keeping up?" needs the
past instead, so each state change is appended here and never edited.

Nothing in this module changes a task. It only remembers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database, from_iso, to_iso
from bot.parser import DEFAULT_TZ

CREATED = "created"
DONE = "done"
REOPENED = "reopened"
DROPPED = "dropped"
WAITING = "waiting"
RESCHEDULED = "rescheduled"


@dataclass(frozen=True)
class Event:
    task_id: int
    kind: str
    at: datetime
    actor_id: int | None


def record(
    db: Database, *, task_id: int, kind: str, at: datetime, actor_id: int | None = None
) -> None:
    """Append one thing that happened. Never raises: history must not break a write."""
    db.execute(
        "INSERT INTO task_events (task_id, kind, at, actor_id) VALUES (?, ?, ?, ?)",
        (task_id, kind, to_iso(at), actor_id),
    )


def since(db: Database, *, kind: str | None = None, after: datetime) -> list[Event]:
    sql = "SELECT * FROM task_events WHERE at >= ?"
    params: list[object] = [to_iso(after)]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    rows = db.query(sql + " ORDER BY at", params)
    return [
        Event(
            task_id=int(row["task_id"]),
            kind=str(row["kind"]),
            at=from_iso(str(row["at"])) or after,
            actor_id=None if row["actor_id"] is None else int(row["actor_id"]),
        )
        for row in rows
    ]


def per_day(
    db: Database,
    *,
    kind: str,
    days: int,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    actor_id: int | None = None,
) -> dict[date, int]:
    """A count for each of the last `days` local days, zeroes included.

    Zeroes matter: a chart with the quiet days missing tells a flattering lie.
    """
    today = now.astimezone(tz).date()
    window = {today - timedelta(days=offset): 0 for offset in range(days)}
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), tzinfo=tz)
    for event in since(db, kind=kind, after=start):
        if actor_id is not None and event.actor_id != actor_id:
            continue
        day = event.at.astimezone(tz).date()
        if day in window:
            window[day] += 1
    return dict(sorted(window.items()))


def streak(db: Database, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ, limit: int = 365) -> int:
    """Consecutive days ending today (or yesterday) with at least one completion.

    Today not counting against you until it is over is the difference between a
    streak that motivates and one that punishes you at 00:01.
    """
    done = per_day(db, kind=DONE, days=limit, now=now, tz=tz)
    today = now.astimezone(tz).date()
    if not any(done.values()):
        return 0
    start = today if done.get(today) else today - timedelta(days=1)
    count = 0
    day = start
    while done.get(day):
        count += 1
        day -= timedelta(days=1)
    return count


def weekday_pattern(
    db: Database, *, kind: str, days: int, now: datetime, tz: ZoneInfo = DEFAULT_TZ
) -> Counter[int]:
    """How the work falls across Monday…Sunday, for the "you slip on Sundays" insight."""
    counts: Counter[int] = Counter()
    for day, count in per_day(db, kind=kind, days=days, now=now, tz=tz).items():
        counts[day.weekday()] += count
    return counts
