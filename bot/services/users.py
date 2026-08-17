"""The two people who may use this bot.

`ALLOWED_USER_IDS` decides who is allowed in; this module turns an allowed sender
into a row in `users`, because every task needs an owner that exists. The `short`
is derived from the Telegram username the first time somebody is seen, and never
changes afterwards — templates and `@mentions` refer to it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace

from bot.db import Database

_SHORT_CLEAN = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class User:
    telegram_id: int
    short: str
    display_name: str
    dm_chat_id: int | None
    digest_hour: int
    quiet_start: str
    quiet_end: str
    escalation: bool


def row_to_user(row: sqlite3.Row) -> User:
    return User(
        telegram_id=int(row["telegram_id"]),
        short=str(row["short"]),
        display_name=str(row["display_name"]),
        dm_chat_id=None if row["dm_chat_id"] is None else int(row["dm_chat_id"]),
        digest_hour=int(row["digest_hour"]),
        quiet_start=str(row["quiet_start"]),
        quiet_end=str(row["quiet_end"]),
        escalation=bool(row["escalation"]),
    )


def get_user(db: Database, telegram_id: int) -> User | None:
    row = db.query_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    return None if row is None else row_to_user(row)


def get_by_short(db: Database, short: str) -> User | None:
    row = db.query_one("SELECT * FROM users WHERE short = ?", (short.lower(),))
    return None if row is None else row_to_user(row)


def list_users(db: Database) -> list[User]:
    return [row_to_user(row) for row in db.query("SELECT * FROM users ORDER BY short")]


def partner_of(db: Database, telegram_id: int) -> User | None:
    """The other half of the couple, for the "Give to …" button.

    There are exactly two users, so "the other one" is unambiguous; it is None
    until the second person has sent their first message.
    """
    row = db.query_one(
        "SELECT * FROM users WHERE telegram_id != ? ORDER BY short LIMIT 1", (telegram_id,)
    )
    return None if row is None else row_to_user(row)


def known_shorts(db: Database) -> frozenset[str]:
    return frozenset(str(row["short"]) for row in db.query("SELECT short FROM users"))


def ensure_user(
    db: Database,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    dm_chat_id: int | None = None,
) -> User:
    """Register the sender if this is the first time we see them, then return them.

    Called from every entry point, so a task can always be owned by whoever wrote
    it. `dm_chat_id` is only filled in from private chats.
    """
    existing = get_user(db, telegram_id)
    if existing is None:
        short = _derive_short(username, first_name, telegram_id, known_shorts(db))
        db.execute(
            "INSERT INTO users (telegram_id, short, display_name, dm_chat_id) VALUES (?, ?, ?, ?)",
            (
                telegram_id,
                short,
                _display_name(username, first_name, last_name, telegram_id),
                dm_chat_id,
            ),
        )
        created = get_user(db, telegram_id)
        if created is None:  # pragma: no cover - the insert above just succeeded
            raise RuntimeError(f"could not register user {telegram_id}")
        return created

    if dm_chat_id is not None and existing.dm_chat_id != dm_chat_id:
        db.execute(
            "UPDATE users SET dm_chat_id = ? WHERE telegram_id = ?", (dm_chat_id, telegram_id)
        )
        return replace(existing, dm_chat_id=dm_chat_id)
    return existing


def _display_name(
    username: str | None, first_name: str | None, last_name: str | None, telegram_id: int
) -> str:
    parts = [part for part in (first_name, last_name) if part]
    if parts:
        return " ".join(parts)
    return username or str(telegram_id)


def _derive_short(
    username: str | None, first_name: str | None, telegram_id: int, taken: frozenset[str]
) -> str:
    """A lowercase handle for @mentions and template `owner:` fields."""
    base = _SHORT_CLEAN.sub("", (username or first_name or "").lower()) or f"u{telegram_id}"
    if base not in taken:
        return base
    return f"{base}{telegram_id}"
