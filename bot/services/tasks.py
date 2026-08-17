"""Creating, listing and completing tasks.

All of the Phase 1 logic lives here so that the handlers stay a thin translation
layer between Telegram and these functions, and so the tests never need a bot.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database, from_iso, to_iso
from bot.parser import PARIS, ParsedTask, parse_task
from bot.services.users import User, get_by_short, known_shorts

#: Statuses that still need somebody to do something.
OPEN_STATUSES = ("todo", "waiting")

#: How far back `/add` looks for the project of the sender's previous task.
PROJECT_MEMORY = timedelta(minutes=30)


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
) -> CreateOutcome:
    """Parse a free-text message and store the task it describes."""
    parsed = parse_task(
        text,
        now=now,
        sender_short=sender.short,
        known_shorts=known_shorts(db),
        default_project=recent_project(db, sender.telegram_id, now=now),
        tz=tz,
    )
    if not parsed.ok:
        return CreateOutcome(task=None, warnings=parsed.warnings, error=parsed.error)

    task = create_task(
        db,
        title=parsed.title,
        owner_id=_resolve_owner(db, parsed, sender),
        created_by=sender.telegram_id,
        now=now,
        project=parsed.project,
        due_at=parsed.due_at,
        remind_at=parsed.remind_at,
    )
    return CreateOutcome(task=task, warnings=parsed.warnings)


def complete_task(db: Database, task_id: int, *, now: datetime) -> CompleteOutcome:
    """Mark a task done. Unblock checks arrive with the dependency phase."""
    task = get_task(db, task_id)
    if task is None:
        return CompleteOutcome(task=None)
    if task.status == "done":
        return CompleteOutcome(task=task, already_done=True)
    db.execute("UPDATE tasks SET status = 'done', done_at = ? WHERE id = ?", (to_iso(now), task_id))
    return CompleteOutcome(task=get_task(db, task_id))


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


def _end_of_day(now: datetime, tz: ZoneInfo) -> datetime:
    """The first instant of tomorrow, local time — the exclusive end of 'today'."""
    tomorrow: date = now.astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=tz)


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)
