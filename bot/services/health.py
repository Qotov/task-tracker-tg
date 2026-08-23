"""What `/health` reports: is this thing actually working?

Everything here is read-only and cheap. It answers the question you have after a
deploy — is the scheduler alive, can the bot reach both people, is anything stuck
in the queue — without making you read a log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.db import Database, from_iso
from bot.parser import DEFAULT_TZ
from bot.services import outbox
from bot.services.settings import (
    DASHBOARD_MESSAGE_ID,
    LAST_TICK,
    get_int,
    get_setting,
    group_chat_id,
)
from bot.services.users import User, list_users


@dataclass(frozen=True)
class Health:
    at: datetime
    last_tick: datetime | None
    queued: int
    next_send: datetime | None
    users: tuple[User, ...]
    unreachable: tuple[User, ...]
    group_bound: bool
    dashboard_pinned: bool
    open_tasks: int

    @property
    def tick_age_seconds(self) -> float | None:
        if self.last_tick is None:
            return None
        return (self.at - self.last_tick).total_seconds()

    @property
    def ticking(self) -> bool:
        """The tick runs every 60 seconds; twice that is generous."""
        age = self.tick_age_seconds
        return age is not None and age < 150


def check(db: Database, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> Health:
    del tz  # rendering decides how to show the times
    waiting = outbox.pending(db)
    users = list_users(db)
    return Health(
        at=now,
        last_tick=from_iso(get_setting(db, LAST_TICK)),
        queued=len(waiting),
        next_send=waiting[0].send_after if waiting else None,
        users=tuple(users),
        unreachable=tuple(user for user in users if user.dm_chat_id is None),
        group_bound=group_chat_id(db) is not None,
        dashboard_pinned=get_int(db, DASHBOARD_MESSAGE_ID) is not None,
        open_tasks=_open_count(db),
    )


def _open_count(db: Database) -> int:
    row = db.query_one("SELECT count(*) AS n FROM tasks WHERE status IN ('todo','waiting')")
    return 0 if row is None else int(row["n"])  # pragma: no cover - count always returns a row
