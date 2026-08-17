"""`/export`: the way out of this bot (section 7).

A CSV for a spreadsheet and a JSON dump for anything else. Both are built in
memory — nothing is written to disk — and both include every task, closed ones
too, because an export that quietly omits history is not an export.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from bot.db import Database

#: Column order in the CSV. Note what is absent: there is no priority.
COLUMNS = (
    "id",
    "parent_id",
    "title",
    "owner",
    "status",
    "project",
    "due_at",
    "remind_at",
    "follow_up_at",
    "recurrence",
    "notes",
    "created_by",
    "created_at",
    "done_at",
    "blocked_by",
)


def _rows(db: Database) -> list[dict[str, Any]]:
    shorts = {
        int(row["telegram_id"]): str(row["short"])
        for row in db.query("SELECT telegram_id, short FROM users")
    }
    blockers: dict[int, list[int]] = {}
    for row in db.query("SELECT task_id, depends_on_id FROM task_deps ORDER BY depends_on_id"):
        blockers.setdefault(int(row["task_id"]), []).append(int(row["depends_on_id"]))

    exported = []
    for row in db.query("SELECT * FROM tasks ORDER BY id"):
        task_id = int(row["id"])
        exported.append(
            {
                "id": task_id,
                "parent_id": row["parent_id"],
                "title": row["title"],
                "owner": shorts.get(int(row["owner_id"]), str(row["owner_id"])),
                "status": row["status"],
                "project": row["project"],
                "due_at": row["due_at"],
                "remind_at": row["remind_at"],
                "follow_up_at": row["follow_up_at"],
                "recurrence": row["recurrence"],
                "notes": row["notes"],
                "created_by": shorts.get(int(row["created_by"]), str(row["created_by"])),
                "created_at": row["created_at"],
                "done_at": row["done_at"],
                "blocked_by": " ".join(f"#{blocker}" for blocker in blockers.get(task_id, [])),
            }
        )
    return exported


def export_csv(db: Database) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_rows(db))
    return buffer.getvalue()


def export_json(db: Database) -> str:
    """Tasks plus the attachment index, so the vault can be rebuilt from the file ids."""
    attachments = [
        {
            "id": int(row["id"]),
            "task_id": row["task_id"],
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "mime": row["mime"],
            "kind": row["kind"],
            "caption": row["caption"],
            "added_at": row["added_at"],
        }
        for row in db.query("SELECT * FROM attachments ORDER BY id")
    ]
    return json.dumps(
        {"tasks": _rows(db), "attachments": attachments}, indent=2, ensure_ascii=False
    )
