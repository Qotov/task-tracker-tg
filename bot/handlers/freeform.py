"""Plain text messages.

The whole point of the bot: write "call the landlord tomorrow #move" in the group
and a task exists. Registered last so every command, and the subtask dialogue,
has already had its chance at the message.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Config
from bot.db import Database
from bot.handlers import answer_creation, register_sender
from bot.services import tasks as task_service

router = Router(name="freeform")


@router.message(F.text & ~F.text.startswith("/"))
async def plain_text_task(message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    text = (message.text or "").strip()
    if not text:
        return

    now = datetime.now(UTC)
    outcome = task_service.create_from_text(db, text, sender=user, now=now, tz=config.tz)
    await answer_creation(message, outcome, db, now=now, config=config)
