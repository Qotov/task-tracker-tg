"""Slash commands. Each one parses its arguments, calls a service, renders the answer."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import answer_creation, owner_of, register_sender, send_card
from bot.parser import parse_task_ref, parse_when
from bot.services import tasks as task_service
from bot.services.users import User, get_by_short, list_users

router = Router(name="commands")

ADD_USAGE = "Tell me what to add, like <code>/add call the mairie tomorrow #mother-visa</code>."
SUB_USAGE = "Use <code>/sub 12 pay the timbre fiscal</code>."
DUE_USAGE = "Use <code>/due 12 tomorrow</code> or <code>/due 12 20/09</code>."
OWN_USAGE = "Use <code>/own 12 @sasha</code>."
NOTE_USAGE = "Use <code>/note 12 they asked for a payslip</code>."
NO_DATE = (
    "I did not recognise a date in that. "
    "Try <code>tomorrow</code>, <code>20/09</code>, or <code>+3d</code>."
)
UNKNOWN_OWNER = "I do not know <b>@{short}</b>. They need to send me /start first."


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
    await answer_creation(message, outcome, db, now=now, config=config)


@router.message(Command("sub"))
async def cmd_sub(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    task_id, rest = _split_ref(command.args)
    if task_id is None or not rest:
        await message.answer(SUB_USAGE)
        return
    parent = task_service.get_task(db, task_id)
    if parent is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return

    now = datetime.now(UTC)
    outcome = task_service.create_from_text(
        db, rest, sender=user, now=now, tz=config.tz, parent=parent
    )
    await answer_creation(message, outcome, db, now=now, config=config)


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


@router.message(Command("drop"))
async def cmd_drop(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id = parse_task_ref(command.args or "")
    if task_id is None:
        await message.answer(render.BAD_TASK_REF)
        return

    task = task_service.drop_task(db, task_id)
    if task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    await send_card(message, db, task, now=datetime.now(UTC), config=config, lead="🗑 Dropped\n")


@router.message(Command("due"))
async def cmd_due(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id, rest = _split_ref(command.args)
    if task_id is None or not rest:
        await message.answer(DUE_USAGE)
        return

    now = datetime.now(UTC)
    due_at = parse_when(rest, now=now, tz=config.tz)
    if due_at is None:
        await message.answer(NO_DATE)
        return

    task = task_service.set_due(db, task_id, due_at=due_at)
    if task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    await send_card(message, db, task, now=now, config=config, lead="📅 Moved\n")


@router.message(Command("own"))
async def cmd_own(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    sender = register_sender(message, db)
    if sender is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    task_id, rest = _split_ref(command.args)
    if task_id is None or not rest:
        await message.answer(OWN_USAGE)
        return

    short = rest.split()[0].lstrip("@").lower()
    new_owner = sender if short == "me" else get_by_short(db, short)
    if new_owner is None:
        await message.answer(UNKNOWN_OWNER.format(short=short))
        return

    task = task_service.set_owner(db, task_id, owner_id=new_owner.telegram_id)
    if task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    await send_card(
        message, db, task, now=datetime.now(UTC), config=config, lead="👤 Handed over\n"
    )


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id, rest = _split_ref(command.args)
    if task_id is None or not rest:
        await message.answer(NOTE_USAGE)
        return

    now = datetime.now(UTC)
    task = task_service.append_note(db, task_id, text=rest, now=now, tz=config.tz)
    if task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    await send_card(message, db, task, now=now, config=config, lead="📝 Noted\n")


@router.message(Command("today"))
async def cmd_today(message: Message, db: Database, config: Config) -> None:
    register_sender(message, db)
    now = datetime.now(UTC)
    due = task_service.list_due_today(db, now=now, tz=config.tz)
    await message.answer(render.today_list(due, _owners(db), now=now, tz=config.tz))


@router.message(Command("week"))
async def cmd_week(message: Message, db: Database, config: Config) -> None:
    register_sender(message, db)
    now = datetime.now(UTC)
    due = task_service.list_week(db, now=now, tz=config.tz)
    await message.answer(render.week_list(due, _owners(db), now=now, tz=config.tz))


@router.message(Command("overdue"))
async def cmd_overdue(message: Message, db: Database, config: Config) -> None:
    register_sender(message, db)
    now = datetime.now(UTC)
    late = task_service.list_overdue(db, now=now)
    await message.answer(render.overdue_list(late, _owners(db), now=now, tz=config.tz))


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


def _split_ref(args: str | None) -> tuple[int | None, str]:
    """Split `12 the rest of it` into the task id and the remainder."""
    head, _, rest = (args or "").strip().partition(" ")
    return parse_task_ref(head), rest.strip()


def _owners(db: Database) -> dict[int, User]:
    return {user.telegram_id: user for user in list_users(db)}
