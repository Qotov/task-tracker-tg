"""The document vault: scans and PDFs kept against a task (section 14).

Telegram already stores the file. We keep its `file_id` and enough words to find
it again — the task it belongs to, the project, the file name, the caption. The
bot never downloads anything to disk, so nothing sensitive lands on the server.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from bot.db import Database, from_iso, to_iso
from bot.services.tasks import OPEN_STATUSES, Task, row_to_task

#: How many recent tasks the intake buttons offer.
RECENT_OFFERED = 5

#: How many files `/docs` sends back at most (section 14).
SEARCH_LIMIT = 10


@dataclass(frozen=True)
class Attachment:
    id: int
    task_id: int | None
    file_id: str
    file_unique_id: str
    file_name: str | None
    mime: str | None
    kind: str
    caption: str | None
    added_by: int
    added_at: datetime


def row_to_attachment(row: sqlite3.Row) -> Attachment:
    added_at = from_iso(str(row["added_at"]))
    if added_at is None:  # pragma: no cover - the column is NOT NULL
        raise ValueError(f"attachment {row['id']} has no added_at")
    return Attachment(
        id=int(row["id"]),
        task_id=None if row["task_id"] is None else int(row["task_id"]),
        file_id=str(row["file_id"]),
        file_unique_id=str(row["file_unique_id"]),
        file_name=None if row["file_name"] is None else str(row["file_name"]),
        mime=None if row["mime"] is None else str(row["mime"]),
        kind=str(row["kind"]),
        caption=None if row["caption"] is None else str(row["caption"]),
        added_by=int(row["added_by"]),
        added_at=added_at,
    )


def store(
    db: Database,
    *,
    file_id: str,
    file_unique_id: str,
    kind: str,
    added_by: int,
    added_at: datetime,
    task_id: int | None = None,
    file_name: str | None = None,
    mime: str | None = None,
    caption: str | None = None,
) -> Attachment:
    """Remember a file. Only the id — Telegram keeps the bytes."""
    attachment_id = db.insert(
        """
        INSERT INTO attachments
            (task_id, file_id, file_unique_id, file_name, mime, kind, caption, added_by, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            file_id,
            file_unique_id,
            file_name,
            mime,
            kind,
            caption,
            added_by,
            to_iso(added_at),
        ),
    )
    stored = get_attachment(db, attachment_id)
    if stored is None:  # pragma: no cover - the insert just succeeded
        raise RuntimeError(f"attachment {attachment_id} vanished")
    return stored


def get_attachment(db: Database, attachment_id: int) -> Attachment | None:
    row = db.query_one("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    return None if row is None else row_to_attachment(row)


def attach_to(db: Database, attachment_id: int, task_id: int | None) -> Attachment | None:
    """File it against a task, or against nothing at all."""
    if get_attachment(db, attachment_id) is None:
        return None
    db.execute("UPDATE attachments SET task_id = ? WHERE id = ?", (task_id, attachment_id))
    return get_attachment(db, attachment_id)


def attachments_of(db: Database, task_id: int) -> list[Attachment]:
    rows = db.query("SELECT * FROM attachments WHERE task_id = ? ORDER BY id", (task_id,))
    return [row_to_attachment(row) for row in rows]


def recently_touched(db: Database, limit: int = RECENT_OFFERED) -> list[Task]:
    """The open tasks a file most likely belongs to — the newest ones."""
    rows = db.query(
        f"""
        SELECT * FROM tasks WHERE status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        ORDER BY id DESC LIMIT ?
        """,
        (*OPEN_STATUSES, limit),
    )
    return [row_to_task(row) for row in rows]


def search(db: Database, query: str, *, limit: int = SEARCH_LIMIT) -> list[Attachment]:
    """Look through project, task title, file name and caption, case-insensitively."""
    wanted = f"%{query.strip().lower()}%"
    if not query.strip():
        return []
    rows = db.query(
        """
        SELECT attachments.* FROM attachments
        LEFT JOIN tasks ON tasks.id = attachments.task_id
        WHERE lower(COALESCE(attachments.file_name, '')) LIKE ?
           OR lower(COALESCE(attachments.caption, '')) LIKE ?
           OR lower(COALESCE(tasks.title, '')) LIKE ?
           OR lower(COALESCE(tasks.project, '')) LIKE ?
        ORDER BY attachments.id DESC LIMIT ?
        """,
        (wanted, wanted, wanted, wanted, limit),
    )
    return [row_to_attachment(row) for row in rows]


def search_tasks(db: Database, query: str, *, limit: int = RECENT_OFFERED) -> list[Task]:
    """Find a task to file a document against, by title or project."""
    wanted = f"%{query.strip().lower()}%"
    if not query.strip():
        return []
    rows = db.query(
        f"""
        SELECT * FROM tasks
        WHERE status IN ({", ".join("?" for _ in OPEN_STATUSES)})
          AND (lower(title) LIKE ? OR lower(COALESCE(project, '')) LIKE ?)
        ORDER BY id DESC LIMIT ?
        """,
        (*OPEN_STATUSES, wanted, wanted, limit),
    )
    return [row_to_task(row) for row in rows]
