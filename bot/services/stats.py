"""The numbers behind the tracker board.

Counting happens here, drawing happens in `render.py`. Everything is derived from
one pass over the task table: with two people and a few hundred rows that is far
cheaper than six aggregate queries, and it keeps the rules in one readable place.

Nothing here ranks tasks by anything but their due date. There is no priority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.parser import PARIS
from bot.services.tasks import OPEN_STATUSES, Task, row_to_task
from bot.services.users import User, list_users

#: How many upcoming tasks the board names by title.
UPCOMING_SHOWN = 3

#: How many days of workload the board draws.
HORIZON_DAYS = 7


@dataclass(frozen=True)
class PersonLoad:
    """One person's share of the work."""

    user: User
    due_today: int
    done_today: int
    overdue: int
    waiting: int
    open_total: int


@dataclass(frozen=True)
class DayLoad:
    day: date
    count: int


@dataclass(frozen=True)
class ProjectLoad:
    project: str | None
    count: int


@dataclass(frozen=True)
class Board:
    """Everything the board draws, already counted."""

    at: datetime
    due_today: int
    done_today: int
    overdue: int
    waiting: int
    open_total: int
    people: tuple[PersonLoad, ...] = ()
    days: tuple[DayLoad, ...] = ()
    projects: tuple[ProjectLoad, ...] = ()
    upcoming: tuple[Task, ...] = field(default_factory=tuple)

    @property
    def today_total(self) -> int:
        """Everything that was on the plate for today, finished or not."""
        return self.due_today + self.done_today

    @property
    def is_empty(self) -> bool:
        return self.open_total == 0 and self.done_today == 0


def build_board(db: Database, *, now: datetime, tz: ZoneInfo = PARIS) -> Board:
    """Count today, the week ahead, and who is carrying what."""
    tasks = [row_to_task(row) for row in db.query("SELECT * FROM tasks")]
    users = list_users(db)
    today = now.astimezone(tz).date()
    end_of_today = _start_of(today + timedelta(days=1), tz)

    open_tasks = [task for task in tasks if task.status in OPEN_STATUSES]
    due_today = [t for t in open_tasks if t.due_at is not None and t.due_at < end_of_today]
    done_today = [
        t
        for t in tasks
        if t.status == "done" and t.done_at is not None and t.done_at.astimezone(tz).date() == today
    ]
    overdue = [t for t in open_tasks if t.due_at is not None and t.due_at < now]
    waiting = [t for t in open_tasks if t.status == "waiting"]

    return Board(
        at=now,
        due_today=len(due_today),
        done_today=len(done_today),
        overdue=len(overdue),
        waiting=len(waiting),
        open_total=len(open_tasks),
        people=tuple(
            _person_load(user, due_today, done_today, overdue, waiting, open_tasks)
            for user in users
        ),
        days=_days(open_tasks, today=today, tz=tz),
        projects=_projects(open_tasks),
        upcoming=tuple(_upcoming(open_tasks, now=now)),
    )


def _person_load(
    user: User,
    due_today: list[Task],
    done_today: list[Task],
    overdue: list[Task],
    waiting: list[Task],
    open_tasks: list[Task],
) -> PersonLoad:
    def mine(tasks: list[Task]) -> int:
        return sum(1 for task in tasks if task.owner_id == user.telegram_id)

    return PersonLoad(
        user=user,
        due_today=mine(due_today),
        done_today=mine(done_today),
        overdue=mine(overdue),
        waiting=mine(waiting),
        open_total=mine(open_tasks),
    )


def _days(open_tasks: list[Task], *, today: date, tz: ZoneInfo) -> tuple[DayLoad, ...]:
    """A count per day for the week ahead; anything late lands on today's column."""
    counts = {today + timedelta(days=offset): 0 for offset in range(HORIZON_DAYS)}
    horizon = today + timedelta(days=HORIZON_DAYS)
    for task in open_tasks:
        if task.due_at is None:
            continue
        day = task.due_at.astimezone(tz).date()
        if day < today:
            day = today
        if day < horizon:
            counts[day] += 1
    return tuple(DayLoad(day=day, count=count) for day, count in sorted(counts.items()))


def _projects(open_tasks: list[Task]) -> tuple[ProjectLoad, ...]:
    counts: dict[str | None, int] = {}
    for task in open_tasks:
        counts[task.project] = counts.get(task.project, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0] or "~"))
    return tuple(ProjectLoad(project=project, count=count) for project, count in ordered)


def _upcoming(open_tasks: list[Task], *, now: datetime) -> list[Task]:
    ahead = [task for task in open_tasks if task.due_at is not None and task.due_at >= now]
    ahead.sort(key=lambda task: (task.due_at or now, task.id))
    return ahead[:UPCOMING_SHOWN]


def _start_of(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)
