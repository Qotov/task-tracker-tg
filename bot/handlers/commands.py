"""Slash commands. Each one parses its arguments, calls a service, renders the answer."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import (
    announce_unblocked,
    answer_creation,
    build_view,
    owner_of,
    register_sender,
    send_card,
)
from bot.handlers.callbacks import start_new_task
from bot.parser import parse_task_ref, parse_when
from bot.services import tasks as task_service
from bot.services.users import User, get_by_short

router = Router(name="commands")

#: Labels on the keyboard under the text field, and the view each one opens.
HOME_BUTTONS = {
    render.HOME_TODAY: "today",
    render.HOME_WEEK: "week",
    render.HOME_MONTH: "month",
    render.HOME_OVERDUE: "overdue",
    render.HOME_MINE: "mine",
    render.HOME_BOARD: "board",
    render.HOME_NEW: "new",
    render.HOME_MENU: "menu",
}

ADD_USAGE = "Tell me what to add, like <code>/add call the mairie tomorrow #mother-visa</code>."
SUB_USAGE = "Use <code>/sub 12 pay the timbre fiscal</code>."
DUE_USAGE = "Use <code>/due 12 tomorrow</code> or <code>/due 12 20/09</code>."
OWN_USAGE = "Use <code>/own 12 @sasha</code>."
NOTE_USAGE = "Use <code>/note 12 they asked for a payslip</code>."
WAIT_USAGE = "Use <code>/wait 12</code>, or <code>/wait 12 20/09</code> to chase it up then."
BLOCK_USAGE = "Use <code>/block 12 after 7</code> — 12 waits until 7 is done."
NO_DATE = (
    "I did not recognise a date in that. "
    "Try <code>tomorrow</code>, <code>20/09</code>, or <code>+3d</code>."
)
UNKNOWN_OWNER = "I do not know <b>@{short}</b>. They need to send me /start first."


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    if message.chat.type == ChatType.PRIVATE:
        # The keyboard under the text field is per-chat, so it only makes sense
        # in a private one: in the group it would appear for both of them.
        await message.answer(render.start_text(user), reply_markup=render.home_keyboard())
    else:
        await message.answer(render.start_text(user))
    await _show_view("menu", message, db, user=user, config=config)


@router.message(Command("help"))
async def cmd_help(message: Message, db: Database) -> None:
    register_sender(message, db)
    await message.answer(render.help_text(), reply_markup=render.menu_keyboard())


@router.message(Command("menu"))
async def cmd_menu(message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    await _show_view("menu", message, db, user=user, config=config)


@router.message(Command("board"))
async def cmd_board(message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    await _show_view("board", message, db, user=user, config=config)


@router.message(F.text.in_(HOME_BUTTONS))
async def home_button(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    """The keyboard under the text field sends plain text; map it back to a view."""
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    view = HOME_BUTTONS[message.text or ""]
    if view == "new":
        await start_new_task(message, state)
        return
    await _show_view(view, message, db, user=user, config=config)


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext, db: Database) -> None:
    register_sender(message, db)
    await start_new_task(message, state)


@router.message(Command("month"))
async def cmd_month(message: Message, db: Database, config: Config) -> None:
    await _view_command("month", message, db, config)


@router.message(Command("wait"))
async def cmd_wait(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id, rest = _split_ref(command.args)
    if task_id is None:
        await message.answer(WAIT_USAGE)
        return

    now = datetime.now(UTC)
    follow_up = parse_when(rest, now=now, tz=config.tz) if rest else None
    if rest and follow_up is None:
        await message.answer(NO_DATE)
        return

    task = task_service.start_waiting(db, task_id, now=now, follow_up_at=follow_up, tz=config.tz)
    if task is None:
        await message.answer(render.UNKNOWN_TASK.format(task_id=task_id))
        return
    await send_card(message, db, task, now=now, config=config, lead="⏳ Waiting\n")


@router.message(Command("block"))
async def cmd_block(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    register_sender(message, db)
    task_id, blocker_id = _split_dependency(command.args)
    if task_id is None or blocker_id is None:
        await message.answer(BLOCK_USAGE)
        return

    refused = task_service.add_dependency(db, task_id, blocker_id)
    if refused is not None:
        await message.answer(f"🔒 {refused}")
        return

    task = task_service.get_task(db, task_id)
    if task is None:  # pragma: no cover - add_dependency already checked
        return
    await send_card(message, db, task, now=datetime.now(UTC), config=config, lead="🔒 Blocked\n")


@router.message(Command("settings"))
async def cmd_settings(message: Message, db: Database) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    await message.answer(render.settings_text(user), reply_markup=render.settings_keyboard(user))


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
    announce_unblocked(db, outcome.task, now=now, config=config)
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
    await _view_command("today", message, db, config)


@router.message(Command("week"))
async def cmd_week(message: Message, db: Database, config: Config) -> None:
    await _view_command("week", message, db, config)


@router.message(Command("overdue"))
async def cmd_overdue(message: Message, db: Database, config: Config) -> None:
    await _view_command("overdue", message, db, config)


@router.message(Command("mine"))
async def cmd_mine(message: Message, db: Database, config: Config) -> None:
    await _view_command("mine", message, db, config)


async def _view_command(view: str, message: Message, db: Database, config: Config) -> None:
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    await _show_view(view, message, db, user=user, config=config)


async def _show_view(
    view: str, message: Message, db: Database, *, user: User, config: Config
) -> None:
    text, markup = build_view(view, db, user=user, now=datetime.now(UTC), config=config)
    await message.answer(text, reply_markup=markup)


def _split_dependency(args: str | None) -> tuple[int | None, int | None]:
    """Read `12 after 7`, and `12 7` as well — the word is optional."""
    parts = [part for part in (args or "").split() if part.lower() != "after"]
    if len(parts) != 2:
        return None, None
    return parse_task_ref(parts[0]), parse_task_ref(parts[1])


def _split_ref(args: str | None) -> tuple[int | None, str]:
    """Split `12 the rest of it` into the task id and the remainder."""
    head, _, rest = (args or "").strip().partition(" ")
    return parse_task_ref(head), rest.strip()
