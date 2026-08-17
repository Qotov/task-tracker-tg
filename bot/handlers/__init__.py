"""Telegram-facing glue. Handlers translate updates into service calls, nothing more."""

from __future__ import annotations

from datetime import datetime

from aiogram.enums import ChatType
from aiogram.types import Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.services.tasks import CreateOutcome, Task
from bot.services.users import User, ensure_user, get_user


def register_sender(message: Message, db: Database) -> User | None:
    """Make sure the sender exists in `users`, capturing their DM chat when we are in one.

    Returns None only when Telegram gave us no sender, which the whitelist
    middleware has already made impossible for anything that reaches a handler.
    """
    sender = message.from_user
    if sender is None:  # pragma: no cover - dropped by the whitelist middleware
        return None
    return ensure_user(
        db,
        telegram_id=sender.id,
        username=sender.username,
        first_name=sender.first_name,
        last_name=sender.last_name,
        dm_chat_id=message.chat.id if message.chat.type == ChatType.PRIVATE else None,
    )


def owner_of(db: Database, task: Task) -> User:
    owner = get_user(db, task.owner_id)
    if owner is None:  # pragma: no cover - owner_id is a foreign key
        raise RuntimeError(f"task {task.id} has no owner row")
    return owner


def creation_reply(outcome: CreateOutcome, db: Database, *, now: datetime, config: Config) -> str:
    """The answer to /add and to a plain-text task, including any parser complaints."""
    if outcome.task is None:
        return render.with_warnings(outcome.error or render.BAD_TASK_REF, outcome.warnings)
    return render.with_warnings(
        render.created(outcome.task, owner_of(db, outcome.task), now=now, tz=config.tz),
        outcome.warnings,
    )
