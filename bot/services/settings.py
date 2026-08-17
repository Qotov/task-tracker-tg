"""The `settings` table: single values that belong to the installation, not to a task.

The group chat id and the pinned dashboard's message id live here, so a restart
picks up exactly where the last process left off (section 5).
"""

from __future__ import annotations

from bot.db import Database

GROUP_CHAT_ID = "group_chat_id"
DASHBOARD_MESSAGE_ID = "dashboard_message_id"


def get_setting(db: Database, key: str) -> str | None:
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return None if row is None else str(row["value"])


def set_setting(db: Database, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def clear_setting(db: Database, key: str) -> None:
    db.execute("DELETE FROM settings WHERE key = ?", (key,))


def get_int(db: Database, key: str) -> int | None:
    raw = get_setting(db, key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:  # pragma: no cover - we only ever write integers
        return None


def set_int(db: Database, key: str, value: int) -> None:
    set_setting(db, key, str(value))


def group_chat_id(db: Database) -> int | None:
    """The one group this bot works in, learned the first time it is spoken to there."""
    return get_int(db, GROUP_CHAT_ID)


def bind_group(db: Database, chat_id: int) -> bool:
    """Claim a group, or say whether this one is the group already claimed."""
    known = group_chat_id(db)
    if known is None:
        set_int(db, GROUP_CHAT_ID, chat_id)
        return True
    return known == chat_id
