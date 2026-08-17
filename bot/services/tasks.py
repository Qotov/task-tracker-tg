"""Creating, listing and completing tasks.

All of the Phase 1 logic lives here so that the handlers stay a thin translation
layer between Telegram and these functions, and so the tests never need a bot.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from bot.db import Database, from_iso, to_iso
from bot.parser import (
    DEFAULT_DUE_HOUR,
    DEFAULT_DUE_MINUTE,
    PARIS,
    ParsedTask,
    parse_task,
)
from bot.services.users import User, get_by_short, known_shorts

#: Statuses that still need somebody to do something.
OPEN_STATUSES = ("todo", "waiting")

#: How far back `/add` looks for the project of the sender's previous task.
PROJECT_MEMORY = timedelta(minutes=30)

#: Default distance of a follow-up when a task is parked as `waiting` (section 7).
FOLLOW_UP_DAYS = 7

#: How many days `/week` covers, counting today.
WEEK_DAYS = 7

#: How many days `/month` covers, counting today.
MONTH_DAYS = 30

#: How alike two titles must read before the bot mentions the older one.
SIMILAR_ENOUGH = 0.82


@dataclass(frozen=True)
class Task:
    id: int
    parent_id: int | None
    title: str
    owner_id: int
    status: str
    project: str | None
    due_at: datetime | None
    remind_at: datetime | None
    follow_up_at: datetime | None
    recurrence: str | None
    notes: str | None
    created_by: int
    created_at: datetime
    done_at: datetime | None


@dataclass(frozen=True)
class CreateOutcome:
    """What `/add` or a plain message produced: a task, or a reason it made none."""

    task: Task | None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    duplicate: Task | None = None
    """An open task that looks like the same errand — the other one probably wrote it."""


@dataclass(frozen=True)
class CompleteOutcome:
    task: Task | None
    already_done: bool = False


def row_to_task(row: sqlite3.Row) -> Task:
    created_at = from_iso(str(row["created_at"]))
    if created_at is None:  # pragma: no cover - created_at is NOT NULL
        raise ValueError(f"task {row['id']} has no created_at")
    return Task(
        id=int(row["id"]),
        parent_id=None if row["parent_id"] is None else int(row["parent_id"]),
        title=str(row["title"]),
        owner_id=int(row["owner_id"]),
        status=str(row["status"]),
        project=None if row["project"] is None else str(row["project"]),
        due_at=from_iso(row["due_at"]),
        remind_at=from_iso(row["remind_at"]),
        follow_up_at=from_iso(row["follow_up_at"]),
        recurrence=None if row["recurrence"] is None else str(row["recurrence"]),
        notes=None if row["notes"] is None else str(row["notes"]),
        created_by=int(row["created_by"]),
        created_at=created_at,
        done_at=from_iso(row["done_at"]),
    )


def get_task(db: Database, task_id: int) -> Task | None:
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return None if row is None else row_to_task(row)


def create_task(
    db: Database,
    *,
    title: str,
    owner_id: int,
    created_by: int,
    now: datetime,
    project: str | None = None,
    due_at: datetime | None = None,
    remind_at: datetime | None = None,
    parent_id: int | None = None,
) -> Task:
    """Insert one task. Exactly one owner, always — the schema enforces the rest."""
    task_id = db.insert(
        """
        INSERT INTO tasks (parent_id, title, owner_id, status, project, due_at, remind_at,
                           created_by, created_at)
        VALUES (?, ?, ?, 'todo', ?, ?, ?, ?, ?)
        """,
        (
            parent_id,
            title,
            owner_id,
            project,
            to_iso(due_at),
            to_iso(remind_at),
            created_by,
            to_iso(now),
        ),
    )
    created = get_task(db, task_id)
    if created is None:  # pragma: no cover - the insert above just succeeded
        raise RuntimeError(f"task {task_id} vanished right after being created")
    return created


def create_from_text(
    db: Database,
    text: str,
    *,
    sender: User,
    now: datetime,
    tz: ZoneInfo = PARIS,
    parent: Task | None = None,
) -> CreateOutcome:
    """Parse a free-text message and store the task it describes.

    With a `parent`, the result is a subtask that inherits the parent's project and
    owner unless the text names its own (section 7).
    """
    parent_owner = None if parent is None else _short_of(db, parent.owner_id)
    parsed = parse_task(
        text,
        now=now,
        sender_short=sender.short,
        known_shorts=known_shorts(db),
        default_project=(
            parent.project
            if parent is not None
            else recent_project(db, sender.telegram_id, now=now)
        ),
        default_owner=parent_owner,
        tz=tz,
    )
    if not parsed.ok:
        return CreateOutcome(task=None, warnings=parsed.warnings, error=parsed.error)

    duplicate = find_similar(db, parsed.title)
    task = create_task(
        db,
        title=parsed.title,
        owner_id=_resolve_owner(db, parsed, sender),
        created_by=sender.telegram_id,
        now=now,
        project=parsed.project,
        due_at=parsed.due_at,
        remind_at=parsed.remind_at,
        parent_id=None if parent is None else parent.id,
    )
    return CreateOutcome(task=task, warnings=parsed.warnings, duplicate=duplicate)


def find_similar(db: Database, title: str, *, threshold: float = SIMILAR_ENOUGH) -> Task | None:
    """An open task that reads like the same errand.

    Two people adding "call the landlord" from two rooms is the failure mode this
    exists for. It never blocks the second task — it only points at the first.
    """
    wanted = _comparable(title)
    if len(wanted) < 6:
        return None
    best: Task | None = None
    best_ratio = threshold
    for task in _open_tasks(db):
        ratio = SequenceMatcher(None, wanted, _comparable(task.title)).ratio()
        if ratio >= best_ratio:
            best, best_ratio = task, ratio
    return best


def copy_for(db: Database, task: Task, *, owner_id: int, created_by: int, now: datetime) -> Task:
    """The same errand on the other person's list — two tasks, one owner each.

    A job both of them have to do (two signatures, two appointments) cannot be one
    task: `owner_id` is singular and stays that way.
    """
    return create_task(
        db,
        title=task.title,
        owner_id=owner_id,
        created_by=created_by,
        now=now,
        project=task.project,
        due_at=task.due_at,
        remind_at=task.remind_at,
        parent_id=task.parent_id,
    )


def _open_tasks(db: Database) -> list[Task]:
    rows = db.query(
        f"SELECT * FROM tasks WHERE status IN ({_placeholders(OPEN_STATUSES)})", OPEN_STATUSES
    )
    return [row_to_task(row) for row in rows]


def _comparable(title: str) -> str:
    """Lowercase words only, so punctuation and spacing do not hide a repeat."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def complete_task(db: Database, task_id: int, *, now: datetime) -> CompleteOutcome:
    """Mark a task done. Unblock checks arrive with the dependency phase."""
    task = get_task(db, task_id)
    if task is None:
        return CompleteOutcome(task=None)
    if task.status == "done":
        return CompleteOutcome(task=task, already_done=True)
    db.execute("UPDATE tasks SET status = 'done', done_at = ? WHERE id = ?", (to_iso(now), task_id))
    return CompleteOutcome(task=get_task(db, task_id))


def set_due(db: Database, task_id: int, *, due_at: datetime | None) -> Task | None:
    """Move a task's due date. `remind_at` follows it, as it does at creation."""
    if get_task(db, task_id) is None:
        return None
    stored = to_iso(due_at)
    db.execute("UPDATE tasks SET due_at = ?, remind_at = ? WHERE id = ?", (stored, stored, task_id))
    return get_task(db, task_id)


def shift_due(
    db: Database, task_id: int, *, days: int, now: datetime, tz: ZoneInfo = PARIS
) -> Task | None:
    """Push a due date out by whole days, keeping the wall-clock time across DST.

    A task with no date at all starts from 09:00 today, so "+1 day" on it means
    tomorrow morning rather than this time tomorrow.
    """
    task = get_task(db, task_id)
    if task is None:
        return None
    if task.due_at is None:
        base = datetime.combine(
            now.astimezone(tz).date(), time(DEFAULT_DUE_HOUR, DEFAULT_DUE_MINUTE), tzinfo=tz
        )
    else:
        base = task.due_at.astimezone(tz)
    return set_due(db, task_id, due_at=(base + timedelta(days=days)).astimezone(UTC))


def set_due_date(
    db: Database, task_id: int, *, day: date, now: datetime, tz: ZoneInfo = PARIS
) -> Task | None:
    """Move a task to a given day, keeping the time of day it already had.

    The reschedule buttons use this: "Tomorrow" on a 14:30 task means 14:30
    tomorrow, and on a task with no time at all it means the usual 09:00.
    """
    task = get_task(db, task_id)
    if task is None:
        return None
    clock = (
        task.due_at.astimezone(tz).timetz()
        if task.due_at is not None
        else time(DEFAULT_DUE_HOUR, DEFAULT_DUE_MINUTE, tzinfo=tz)
    )
    due_local = datetime.combine(day, clock.replace(tzinfo=None), tzinfo=tz)
    return set_due(db, task_id, due_at=due_local.astimezone(UTC))


def set_owner(db: Database, task_id: int, *, owner_id: int) -> Task | None:
    """Hand a task over. Still exactly one owner — this replaces, never adds."""
    if get_task(db, task_id) is None:
        return None
    db.execute("UPDATE tasks SET owner_id = ? WHERE id = ?", (owner_id, task_id))
    return get_task(db, task_id)


def append_note(
    db: Database, task_id: int, *, text: str, now: datetime, tz: ZoneInfo = PARIS
) -> Task | None:
    """Add one dated line to the notes, keeping whatever was there before."""
    task = get_task(db, task_id)
    if task is None:
        return None
    line = f"{now.astimezone(tz):%Y-%m-%d}: {text.strip()}"
    notes = line if not task.notes else f"{task.notes}\n{line}"
    db.execute("UPDATE tasks SET notes = ? WHERE id = ?", (notes, task_id))
    return get_task(db, task_id)


def drop_task(db: Database, task_id: int) -> Task | None:
    """Abandon a task. `done_at` stays empty: dropped is not done."""
    if get_task(db, task_id) is None:
        return None
    db.execute("UPDATE tasks SET status = 'dropped' WHERE id = ?", (task_id,))
    return get_task(db, task_id)


def start_waiting(
    db: Database,
    task_id: int,
    *,
    now: datetime,
    follow_up_at: datetime | None = None,
    tz: ZoneInfo = PARIS,
) -> Task | None:
    """Park a task as `waiting` on somebody else, with a date to chase it up."""
    if get_task(db, task_id) is None:
        return None
    when = follow_up_at or _in_days(now, FOLLOW_UP_DAYS, tz)
    db.execute(
        "UPDATE tasks SET status = 'waiting', follow_up_at = ? WHERE id = ?",
        (to_iso(when), task_id),
    )
    return get_task(db, task_id)


def shift_follow_up(
    db: Database, task_id: int, *, days: int, now: datetime, tz: ZoneInfo = PARIS
) -> Task | None:
    """Give a waiting task more rope, measured from its current follow-up date."""
    task = get_task(db, task_id)
    if task is None:
        return None
    base = task.follow_up_at.astimezone(tz) if task.follow_up_at else now.astimezone(tz)
    when = (base + timedelta(days=days)).astimezone(UTC)
    db.execute("UPDATE tasks SET follow_up_at = ? WHERE id = ?", (to_iso(when), task_id))
    return get_task(db, task_id)


def back_to_todo(db: Database, task_id: int) -> Task | None:
    """Un-park a task: the follow-up disappears with the waiting status."""
    if get_task(db, task_id) is None:
        return None
    db.execute(
        "UPDATE tasks SET status = 'todo', follow_up_at = NULL, done_at = NULL WHERE id = ?",
        (task_id,),
    )
    return get_task(db, task_id)


def blockers_of(db: Database, task_id: int) -> list[Task]:
    """The dependencies of this task that are not finished yet (section 10)."""
    rows = db.query(
        """
        SELECT tasks.* FROM task_deps
        JOIN tasks ON tasks.id = task_deps.depends_on_id
        WHERE task_deps.task_id = ? AND tasks.status NOT IN ('done', 'dropped')
        ORDER BY tasks.id
        """,
        (task_id,),
    )
    return [row_to_task(row) for row in rows]


def is_blocked(db: Database, task_id: int) -> bool:
    """A blocked task is greyed in listings and never generates a reminder."""
    return bool(blockers_of(db, task_id))


def list_subtasks(db: Database, parent_id: int) -> list[Task]:
    rows = db.query(
        "SELECT * FROM tasks WHERE parent_id = ? ORDER BY due_at IS NULL, due_at, id", (parent_id,)
    )
    return [row_to_task(row) for row in rows]


def list_due_today(db: Database, *, now: datetime, tz: ZoneInfo = PARIS) -> list[Task]:
    """Everything due today or earlier and still open, both users, earliest first."""
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE status IN ({_placeholders(OPEN_STATUSES)})
          AND due_at IS NOT NULL AND due_at < ?
        ORDER BY due_at, id
        """,
        (*OPEN_STATUSES, to_iso(_end_of_day(now, tz))),
    )
    return [row_to_task(row) for row in rows]


def list_open_for(db: Database, owner_id: int) -> list[Task]:
    """Open tasks owned by one person: dated ones first, then the undated ones."""
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE owner_id = ? AND status IN ({_placeholders(OPEN_STATUSES)})
        ORDER BY due_at IS NULL, due_at, id
        """,
        (owner_id, *OPEN_STATUSES),
    )
    return [row_to_task(row) for row in rows]


def list_week(db: Database, *, now: datetime, tz: ZoneInfo = PARIS) -> list[Task]:
    """Open tasks due in the next seven days, counting today. Earlier ones are `/overdue`."""
    return list_ahead(db, days=WEEK_DAYS, now=now, tz=tz)


def list_month(db: Database, *, now: datetime, tz: ZoneInfo = PARIS) -> list[Task]:
    """The next thirty days — far enough out to see a mairie appointment coming."""
    return list_ahead(db, days=MONTH_DAYS, now=now, tz=tz)


def list_ahead(db: Database, *, days: int, now: datetime, tz: ZoneInfo = PARIS) -> list[Task]:
    """Open, dated tasks from the start of today to `days` later, earliest first."""
    start = datetime.combine(now.astimezone(tz).date(), time.min, tzinfo=tz)
    end = start + timedelta(days=days)
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE status IN ({_placeholders(OPEN_STATUSES)})
          AND due_at IS NOT NULL AND due_at >= ? AND due_at < ?
        ORDER BY due_at, id
        """,
        (*OPEN_STATUSES, to_iso(start.astimezone(UTC)), to_iso(end.astimezone(UTC))),
    )
    return [row_to_task(row) for row in rows]


def list_overdue(db: Database, *, now: datetime) -> list[Task]:
    """Everything past its due date and not finished, oldest first."""
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE status IN ({_placeholders(OPEN_STATUSES)})
          AND due_at IS NOT NULL AND due_at < ?
        ORDER BY due_at, id
        """,
        (*OPEN_STATUSES, to_iso(now)),
    )
    return [row_to_task(row) for row in rows]


def recent_project(db: Database, user_id: int, *, now: datetime) -> str | None:
    """The project of the last task this person created recently, for `#`-less messages."""
    row = db.query_one(
        """
        SELECT project FROM tasks
        WHERE created_by = ? AND project IS NOT NULL AND created_at >= ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (user_id, to_iso(now - PROJECT_MEMORY)),
    )
    return None if row is None else str(row["project"])


def _resolve_owner(db: Database, parsed: ParsedTask, sender: User) -> int:
    if parsed.owner == sender.short:
        return sender.telegram_id
    owner = get_by_short(db, parsed.owner)
    return sender.telegram_id if owner is None else owner.telegram_id


def _short_of(db: Database, user_id: int) -> str | None:
    row = db.query_one("SELECT short FROM users WHERE telegram_id = ?", (user_id,))
    return None if row is None else str(row["short"])


def _in_days(now: datetime, days: int, tz: ZoneInfo) -> datetime:
    """`days` from now, keeping the wall-clock time even across a DST change."""
    return (now.astimezone(tz) + timedelta(days=days)).astimezone(UTC)


def _end_of_day(now: datetime, tz: ZoneInfo) -> datetime:
    """The first instant of tomorrow, local time — the exclusive end of 'today'."""
    tomorrow: date = now.astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=tz)


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)
