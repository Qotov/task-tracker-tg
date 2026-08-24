"""Statistics that answer a question, and the sentences that come out of them.

The rule for everything here: a number nobody would act on does not belong on the
screen. "You closed 23 tasks" is trivia. "You are closing fewer than you add, and
the backlog has grown by nine this fortnight" is something you can do something
about.

Every figure is derived from `task_events`, so reopening a task cannot quietly
rewrite last week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.parser import DEFAULT_TZ
from bot.services import events
from bot.services.tasks import OPEN_STATUSES, Task, row_to_task
from bot.services.users import User, list_users

#: The window most of the report covers.
WINDOW_DAYS = 14

#: An open task older than this, with nothing done to it, is being avoided.
NEGLECTED_DAYS = 21

#: Fewer completions than this in the window and trends are noise, not signal.
MIN_FOR_A_PATTERN = 6

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class PersonScore:
    user: User
    done: int
    added: int


@dataclass(frozen=True)
class Insights:
    """Everything `/stats` shows, already reasoned about."""

    at: datetime
    days: int
    done: tuple[int, ...] = ()
    """Completions per day, oldest first."""
    added: tuple[int, ...] = ()
    done_total: int = 0
    added_total: int = 0
    streak: int = 0
    open_now: int = 0
    overdue_now: int = 0
    on_time: int = 0
    late: int = 0
    oldest_open: Task | None = None
    neglected: tuple[Task, ...] = ()
    people: tuple[PersonScore, ...] = ()
    best_weekday: int | None = None
    worst_weekday: int | None = None
    messages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def keeping_up(self) -> int:
        """Positive means the backlog shrank over the window."""
        return self.done_total - self.added_total

    @property
    def punctuality(self) -> float | None:
        """Share of dated tasks closed on or before their due date."""
        judged = self.on_time + self.late
        return None if judged == 0 else self.on_time / judged

    @property
    def is_empty(self) -> bool:
        return self.done_total == 0 and self.added_total == 0 and self.open_now == 0


def build_insights(
    db: Database, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ, days: int = WINDOW_DAYS
) -> Insights:
    done_by_day = events.per_day(db, kind=events.DONE, days=days, now=now, tz=tz)
    added_by_day = events.per_day(db, kind=events.CREATED, days=days, now=now, tz=tz)
    open_tasks = _open_tasks(db)
    on_time, late = _punctuality(db, now=now, days=days)
    best, worst = _weekdays(db, now=now, days=days, tz=tz)

    report = Insights(
        at=now,
        days=days,
        done=tuple(done_by_day.values()),
        added=tuple(added_by_day.values()),
        done_total=sum(done_by_day.values()),
        added_total=sum(added_by_day.values()),
        streak=events.streak(db, now=now, tz=tz),
        open_now=len(open_tasks),
        overdue_now=sum(1 for t in open_tasks if t.due_at is not None and t.due_at < now),
        on_time=on_time,
        late=late,
        oldest_open=min(open_tasks, key=lambda t: t.created_at, default=None),
        neglected=tuple(_neglected(open_tasks, now=now)),
        people=tuple(_people(db, now=now, days=days, tz=tz)),
        best_weekday=best,
        worst_weekday=worst,
    )
    return _with_messages(report, tz=tz)


def _open_tasks(db: Database) -> list[Task]:
    rows = db.query(
        f"SELECT * FROM tasks WHERE status IN ({', '.join('?' for _ in OPEN_STATUSES)})",
        OPEN_STATUSES,
    )
    return [row_to_task(row) for row in rows]


def _punctuality(db: Database, *, now: datetime, days: int) -> tuple[int, int]:
    """Of the dated tasks closed in the window, how many beat their date."""
    rows = db.query(
        """
        SELECT tasks.due_at, task_events.at FROM task_events
        JOIN tasks ON tasks.id = task_events.task_id
        WHERE task_events.kind = 'done' AND task_events.at >= ? AND tasks.due_at IS NOT NULL
        """,
        ((now - timedelta(days=days)).isoformat(),),
    )
    on_time = sum(1 for row in rows if str(row["at"]) <= str(row["due_at"]))
    return on_time, len(rows) - on_time


def _neglected(open_tasks: list[Task], *, now: datetime) -> list[Task]:
    """Old, undated and untouched — the tasks a list quietly accumulates."""
    cutoff = now - timedelta(days=NEGLECTED_DAYS)
    stale = [task for task in open_tasks if task.created_at < cutoff and task.due_at is None]
    return sorted(stale, key=lambda task: task.created_at)[:5]


def _people(db: Database, *, now: datetime, days: int, tz: ZoneInfo) -> list[PersonScore]:
    scores = []
    for user in list_users(db):
        done = events.per_day(
            db, kind=events.DONE, days=days, now=now, tz=tz, actor_id=user.telegram_id
        )
        added = events.per_day(
            db, kind=events.CREATED, days=days, now=now, tz=tz, actor_id=user.telegram_id
        )
        scores.append(PersonScore(user=user, done=sum(done.values()), added=sum(added.values())))
    return scores


def _weekdays(
    db: Database, *, now: datetime, days: int, tz: ZoneInfo
) -> tuple[int | None, int | None]:
    """The best and worst day of the week, but only once there is enough to mean it."""
    pattern = events.weekday_pattern(db, kind=events.DONE, days=max(days, 28), now=now, tz=tz)
    if sum(pattern.values()) < MIN_FOR_A_PATTERN:
        return None, None
    full = {day: pattern.get(day, 0) for day in range(7)}
    best = max(full, key=lambda day: full[day])
    worst = min(full, key=lambda day: full[day])
    return best, (worst if full[worst] != full[best] else None)


def _with_messages(report: Insights, *, tz: ZoneInfo) -> Insights:
    """Turn the figures into the two or three sentences worth reading."""
    lines: list[str] = []

    if report.done_total or report.added_total:
        if report.keeping_up > 0:
            lines.append(
                f"You are ahead: {report.done_total} closed against "
                f"{report.added_total} added, so the list shrank by {report.keeping_up}."
            )
        elif report.keeping_up < 0:
            lines.append(
                f"The list is growing: {report.added_total} added against "
                f"{report.done_total} closed, {abs(report.keeping_up)} more than you cleared."
            )
        else:
            lines.append(
                f"You are level — {report.done_total} added and {report.done_total} closed."
            )

    share = report.punctuality
    if share is not None:
        judged = report.on_time + report.late
        if share >= 0.8:
            lines.append(f"{report.on_time} of {judged} dated tasks were closed on time.")
        else:
            lines.append(
                f"Only {report.on_time} of {judged} dated tasks made their date — "
                "the dates may be optimistic rather than the work slow."
            )

    if report.best_weekday is not None:
        best = _WEEKDAY_NAMES[report.best_weekday]
        if report.worst_weekday is not None:
            lines.append(
                f"Most gets finished on {best}, least on {_WEEKDAY_NAMES[report.worst_weekday]}."
            )
        else:
            lines.append(f"Most gets finished on {best}.")

    if report.streak >= 3:
        lines.append(f"{report.streak} days running with something closed.")

    if report.neglected:
        oldest = report.neglected[0]
        age = (report.at - oldest.created_at).days
        lines.append(
            f"{len(report.neglected)} open task(s) have sat undated for weeks — "
            f"#{oldest.id} is {age} days old. Give one a date or drop it."
        )

    if report.overdue_now > report.open_now / 2 and report.open_now >= 4:
        lines.append(
            f"{report.overdue_now} of {report.open_now} open tasks are past due. "
            "That usually means the dates, not the effort."
        )

    return Insights(**{**report.__dict__, "messages": tuple(lines)})
