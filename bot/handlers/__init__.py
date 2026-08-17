"""Telegram-facing glue. Handlers translate updates into service calls, nothing more."""

from __future__ import annotations

from datetime import datetime

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.services.tasks import CreateOutcome, Task
from bot.services.users import User, ensure_user, get_user, partner_of


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


def card_text(db: Database, task: Task, *, now: datetime, config: Config) -> str:
    return render.task_card(task, owner_of(db, task), now=now, tz=config.tz)


def card_markup(db: Database, task: Task) -> InlineKeyboardMarkup | None:
    """The buttons belonging to a task, with the partner named on the "Give to" one."""
    return render.task_keyboard(task, partner=partner_of(db, task.owner_id))


async def send_card(
    message: Message, db: Database, task: Task, *, now: datetime, config: Config, lead: str = ""
) -> None:
    text = card_text(db, task, now=now, config=config)
    await message.answer(lead + text, reply_markup=card_markup(db, task))


async def refresh_card(
    message: Message, db: Database, task: Task, *, now: datetime, config: Config
) -> None:
    """Update a card in place (section 13). Telegram rejects an edit that changes nothing."""
    try:
        await message.edit_text(
            card_text(db, task, now=now, config=config), reply_markup=card_markup(db, task)
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def answer_creation(
    message: Message, outcome: CreateOutcome, db: Database, *, now: datetime, config: Config
) -> None:
    """The reply to /add, /sub and a plain-text task: a card, plus any parser complaint."""
    if outcome.task is None:
        await message.answer(
            render.with_warnings(outcome.error or render.BAD_TASK_REF, outcome.warnings)
        )
        return
    text = render.with_warnings(
        "✍️ Added\n" + card_text(db, outcome.task, now=now, config=config), outcome.warnings
    )
    await message.answer(text, reply_markup=card_markup(db, outcome.task))
