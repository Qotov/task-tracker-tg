"""The morning digest, one per person (section 11).

Four sections: what is due today, what is late, what came free in the last day,
and what is waiting on somebody with a follow-up due. When all four are empty the
digest is not sent at all — a daily "nothing to report" trains you to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database, to_iso
from bot.parser import PARIS
from bot.services.tasks import OPEN_STATUSES, Task, is_blocked, row_to_task
from bot.services.users import User

#: How far back the digest looks for tasks that came free.
UNBLOCKED_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Digest:
    """One person's morning, already sorted."""

    user: User
    due_today: tuple[Task, ...] = ()
    overdue: tuple[Task, ...] = ()
    unblocked: tuple[Task, ...] = ()
    follow_ups: tuple[Task, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.due_today or self.overdue or self.unblocked or self.follow_ups)


def build_digest(db: Database, user: User, *, now: datetime, tz: ZoneInfo = PARIS) -> Digest:
    """Everything this person should know when they wake up."""
    local_today = now.astimezone(tz).date()
    end_of_today = datetime.combine(local_today + timedelta(days=1), time.min, tzinfo=tz)

    mine = _open_tasks_of(db, user.telegram_id)
    due_today = [
        task for task in mine if task.due_at is not None and now <= task.due_at < end_of_today
    ]
    overdue = [task for task in mine if task.due_at is not None and task.due_at < now]
    follow_ups = [
        task
        for task in mine
        if task.status == "waiting"
        and task.follow_up_at is not None
        and task.follow_up_at < end_of_today
    ]

    return Digest(
        user=user,
        due_today=tuple(due_today),
        overdue=tuple(overdue),
        unblocked=tuple(_recently_unblocked(db, user.telegram_id, now=now)),
        follow_ups=tuple(follow_ups),
    )


def _open_tasks_of(db: Database, owner_id: int) -> list[Task]:
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE owner_id = ? AND status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        ORDER BY due_at IS NULL, due_at, id
        """,
        (owner_id, *OPEN_STATUSES),
    )
    return [row_to_task(row) for row in rows]


def _recently_unblocked(db: Database, owner_id: int, *, now: datetime) -> list[Task]:
    """Tasks whose last blocker was closed in the past day, and which are free now."""
    rows = db.query(
        """
        SELECT DISTINCT tasks.* FROM tasks
        JOIN task_deps ON task_deps.task_id = tasks.id
        JOIN tasks AS blocker ON blocker.id = task_deps.depends_on_id
        WHERE tasks.owner_id = ? AND tasks.status = 'todo'
          AND blocker.done_at IS NOT NULL AND blocker.done_at >= ?
        ORDER BY tasks.due_at IS NULL, tasks.due_at, tasks.id
        """,
        (owner_id, to_iso(now - UNBLOCKED_WINDOW)),
    )
    return [task for task in (row_to_task(row) for row in rows) if not is_blocked(db, task.id)]
