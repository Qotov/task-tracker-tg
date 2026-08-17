"""Slash commands. Each one parses its arguments, calls a service, renders the answer."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import creation_reply, owner_of, register_sender
from bot.parser import parse_task_ref
from bot.services import tasks as task_service
from bot.services.users import list_users

router = Router(name="commands")

ADD_USAGE = "Tell me what to add, like <code>/add call the mairie tomorrow #mother-visa</code>."


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    await message.answer(render.start_text(user))


@router.message(Command("help"))
async def cmd_help(message: Message, db: Database) -> None:
    register_sender(message, db)
    await message.answer(render.help_text())


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer(ADD_USAGE)
        return

    now = datetime.now(UTC)
    outcome = task_service.create_from_text(db, text, sender=user, now=now, tz=config.tz)
    await message.answer(creation_reply(outcome, db, now=now, config=config))


@router.message(Command("today"))
async def cmd_today(message: Message, db: Database, config: Config) -> None:
    register_sender(message, db)
    now = datetime.now(UTC)
    due = task_service.list_due_today(db, now=now, tz=config.tz)
    owners = {user.telegram_id: user for user in list_users(db)}
    await message.answer(render.today_list(due, owners, now=now, tz=config.tz))


@router.message(Command("mine"))
async def cmd_mine(message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    open_tasks = task_service.list_open_for(db, user.telegram_id)
    await message.answer(
        render.open_list(
            open_tasks,
            title=f"Open tasks for {user.short}",
            now=datetime.now(UTC),
            tz=config.tz,
        )
    )


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id = parse_task_ref(command.args or "")
    if task_id is None:
        await message.answer(render.BAD_TASK_REF)
        return

    now = datetime.now(UTC)
    outcome = task_service.complete_task(db, task_id, now=now)
    if outcome.task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    if outcome.already_done:
        await message.answer(render.already_done(outcome.task))
        return
    await message.answer(
        render.completed(outcome.task, owner_of(db, outcome.task), now=now, tz=config.tz)
    )
