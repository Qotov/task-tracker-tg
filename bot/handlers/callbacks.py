"""Inline button callbacks, menu navigation, and the short dialogues.

Section 13: every press changes something, updates the card in place, and answers
the callback so the spinner on the button stops turning. Between the card buttons
and the menu, nothing in this bot needs a typed command.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import (
    announce_unblocked,
    answer_creation,
    build_view,
    card_markup,
    card_text,
    refresh_card,
    register_presser,
    register_sender,
    send_card,
)
from bot.parser import add_months
from bot.render import MenuAction, SettingAction, TaskAction
from bot.services import tasks as task_service
from bot.services.users import adjust_setting, partner_of

logger = logging.getLogger(__name__)

router = Router(name="callbacks")

#: How long a "send me the text" prompt stays valid (section 13).
PROMPT_TIMEOUT = timedelta(minutes=5)

#: Which button asks for which date, as whole days from today.
_RESCHEDULE_DAYS = {"when_today": 0, "when_tomorrow": 1, "when_3d": 3, "when_1w": 7}

#: The same, in whole months — French paperwork runs on those, not on days.
_RESCHEDULE_MONTHS = {"when_1m": 1, "when_3m": 3}


class TaskInput(StatesGroup):
    """Waiting for the one message that finishes a subtask or a note."""

    waiting_for_text = State()


# --- menu ------------------------------------------------------------------


@router.callback_query(SettingAction.filter())
async def on_setting_button(
    callback: CallbackQuery, callback_data: SettingAction, db: Database
) -> None:
    """Digest hour, quiet hours and escalation, nudged one press at a time."""
    user = register_presser(callback, db)
    if user is None:  # pragma: no cover - whitelisted humans only
        await callback.answer()
        return

    updated = adjust_setting(db, user, field=callback_data.field, step=callback_data.step)
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                render.settings_text(updated), reply_markup=render.settings_keyboard(updated)
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                raise
    await callback.answer()


@router.callback_query(MenuAction.filter())
async def on_menu_button(
    callback: CallbackQuery,
    callback_data: MenuAction,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    """Every view lives in the same message, so the chat does not fill up with lists."""
    if not isinstance(callback.message, Message):  # pragma: no cover - inaccessible message
        await callback.answer()
        return
    user = register_presser(callback, db)
    if user is None:  # pragma: no cover - whitelisted humans only
        await callback.answer()
        return

    if callback_data.view == "new":
        await start_new_task(callback.message, state)
        await callback.answer()
        return

    if callback_data.view == "cancel":
        await state.clear()
        await callback.message.edit_text(render.CANCELLED)
        await callback.answer()
        return

    now = datetime.now(UTC)
    text, markup = build_view(callback_data.view, db, user=user, now=now, config=config)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
    await callback.answer()


# --- task cards ------------------------------------------------------------


@router.callback_query(TaskAction.filter())
async def on_task_button(
    callback: CallbackQuery,
    callback_data: TaskAction,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    """One entry point for every button on a task card."""
    now = datetime.now(UTC)
    task_id = callback_data.task_id
    action = callback_data.action
    card = callback.message if isinstance(callback.message, Message) else None

    task = task_service.get_task(db, task_id)
    if task is None:
        await callback.answer(f"Task #{task_id} is gone.", show_alert=True)
        return

    if action == "open":
        if card is not None:
            await send_card(card, db, task, now=now, config=config)
        await callback.answer()
        return

    if action in {"sub", "note"}:
        await _ask_for_text(callback, state, task_id=task_id, kind=action)
        return

    if action == "both":
        await _copy_for_partner(callback, db, task, now=now, config=config)
        return

    if action in {"when", "when_back"}:
        await _show_keyboard(callback, db, task, submenu=action == "when")
        return

    updated, toast = _apply(action, db, task_id=task_id, now=now, config=config)
    if updated is None:
        await callback.answer(toast, show_alert=True)
        return
    if updated.status == "done":
        announce_unblocked(db, updated, now=now, config=config)
    if card is not None:
        await refresh_card(card, db, updated, now=now, config=config)
    await callback.answer(toast)


def _apply(
    action: str, db: Database, *, task_id: int, now: datetime, config: Config
) -> tuple[task_service.Task | None, str]:
    """Run the button's effect. Returns the updated task, or None with a reason."""
    if action == "done":
        outcome = task_service.complete_task(db, task_id, now=now)
        return outcome.task, "Already done" if outcome.already_done else "Done ✅"
    if action == "day1":
        return task_service.shift_due(db, task_id, days=1, now=now, tz=config.tz), "Moved a day on"
    if action == "day7":
        return (
            task_service.shift_follow_up(db, task_id, days=7, now=now, tz=config.tz),
            "Chasing it up a week later",
        )
    if action == "wait":
        return task_service.start_waiting(db, task_id, now=now, tz=config.tz), "Parked as waiting"
    if action == "todo":
        return task_service.back_to_todo(db, task_id), "Back on the list"
    if action == "reopen":
        return task_service.back_to_todo(db, task_id), "Reopened"
    if action == "drop":
        return task_service.drop_task(db, task_id), "Dropped 🗑"
    if action == "give":
        return _give_away(db, task_id=task_id)
    if action == "when_none":
        return task_service.set_due(db, task_id, due_at=None), "Date cleared"
    if action in _RESCHEDULE_DAYS:
        return _reschedule(
            db, task_id=task_id, days=_RESCHEDULE_DAYS[action], now=now, tz=config.tz
        )
    if action in _RESCHEDULE_MONTHS:
        return _reschedule(
            db, task_id=task_id, months=_RESCHEDULE_MONTHS[action], now=now, tz=config.tz
        )
    logger.warning("unknown callback action %r", action)
    return None, "I do not know that button."


def _give_away(db: Database, *, task_id: int) -> tuple[task_service.Task | None, str]:
    task = task_service.get_task(db, task_id)
    if task is None:  # pragma: no cover - checked by the caller
        return None, render.UNKNOWN_TASK.format(task_id=task_id)
    partner = partner_of(db, task.owner_id)
    if partner is None:
        return None, render.NO_PARTNER
    return (
        task_service.set_owner(db, task_id, owner_id=partner.telegram_id),
        f"Now {partner.short}'s",
    )


def _reschedule(
    db: Database, *, task_id: int, now: datetime, tz: ZoneInfo, days: int = 0, months: int = 0
) -> tuple[task_service.Task | None, str]:
    """Move a task to a day, keeping whatever time of day it already had."""
    target = now.astimezone(tz).date() + timedelta(days=days)
    if months:
        target = add_months(target, months)
    updated = task_service.set_due_date(db, task_id, day=target, now=now, tz=tz)
    if updated is None or updated.due_at is None:  # pragma: no cover - the caller checked
        return updated, "Rescheduled"
    return updated, f"Due {updated.due_at.astimezone(tz):%a %d %b}"


async def _copy_for_partner(
    callback: CallbackQuery,
    db: Database,
    task: task_service.Task,
    *,
    now: datetime,
    config: Config,
) -> None:
    """Some errands need both of them — two tasks, one owner each, never one shared."""
    partner = partner_of(db, task.owner_id)
    if partner is None:
        await callback.answer(render.NO_PARTNER, show_alert=True)
        return
    actor = callback.from_user.id if callback.from_user is not None else task.owner_id
    twin = task_service.copy_for(db, task, owner_id=partner.telegram_id, created_by=actor, now=now)
    if isinstance(callback.message, Message):
        await send_card(callback.message, db, twin, now=now, config=config, lead="👥 Both of you\n")
    await callback.answer(f"Also on {partner.short}'s list as #{twin.id}")


async def _show_keyboard(
    callback: CallbackQuery, db: Database, task: task_service.Task, *, submenu: bool
) -> None:
    """Swap between a card's own buttons and its reschedule row."""
    if isinstance(callback.message, Message):
        markup = render.reschedule_keyboard(task) if submenu else card_markup(db, task)
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest as error:  # pragma: no cover - identical markup
            if "message is not modified" not in str(error):
                raise
    await callback.answer("When?" if submenu else "")


# --- the short dialogues ---------------------------------------------------


async def start_new_task(message: Message, state: FSMContext) -> None:
    """The ➕ button: ask once, and let the parser do the rest of the work."""
    await state.set_state(TaskInput.waiting_for_text)
    await state.update_data(task_id=0, kind="new", asked_at=datetime.now(UTC).isoformat())
    await message.answer(render.NEW_TASK_PROMPT, reply_markup=render.cancel_keyboard())


async def _ask_for_text(
    callback: CallbackQuery, state: FSMContext, *, task_id: int, kind: str
) -> None:
    await state.set_state(TaskInput.waiting_for_text)
    await state.update_data(task_id=task_id, kind=kind, asked_at=datetime.now(UTC).isoformat())
    prompt = render.SUBTASK_PROMPT if kind == "sub" else render.NOTE_PROMPT
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt.format(task_id=task_id))
    await callback.answer()


@router.message(TaskInput.waiting_for_text, F.text)
async def on_prompt_answer(
    message: Message, state: FSMContext, db: Database, config: Config
) -> None:
    """The next message finishes the subtask or the note, then the state clears either way."""
    data = await state.get_data()
    await state.clear()

    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    text = (message.text or "").strip()
    if not text:
        return

    now = datetime.now(UTC)
    kind = str(data.get("kind", "sub"))

    if kind == "new":
        # No deadline on this one: a late answer is still a task, and typing it in
        # a private chat would have made one anyway.
        outcome = task_service.create_from_text(db, text, sender=user, now=now, tz=config.tz)
        await answer_creation(message, outcome, db, now=now, config=config)
        return

    task = task_service.get_task(db, int(data.get("task_id", 0)))
    expired = _has_expired(data.get("asked_at"), now=now)

    if task is None or expired:
        if kind == "note":
            await message.answer(render.NOTE_EXPIRED)
            return
        # Do not lose what they typed: it becomes an ordinary task instead.
        await message.answer(render.SUBTASK_EXPIRED)
        outcome = task_service.create_from_text(db, text, sender=user, now=now, tz=config.tz)
        await answer_creation(message, outcome, db, now=now, config=config)
        return

    if kind == "note":
        noted = task_service.append_note(db, task.id, text=text, now=now, tz=config.tz)
        if noted is not None:
            await message.answer(
                "📝 Noted\n" + card_text(db, noted, now=now, config=config),
                reply_markup=card_markup(db, noted),
            )
        return

    outcome = task_service.create_from_text(
        db, text, sender=user, now=now, tz=config.tz, parent=task
    )
    await answer_creation(message, outcome, db, now=now, config=config)


def _has_expired(asked_at: object, *, now: datetime) -> bool:
    if not isinstance(asked_at, str):  # pragma: no cover - always written with the state
        return True
    try:
        started = datetime.fromisoformat(asked_at)
    except ValueError:  # pragma: no cover - we wrote it ourselves
        return True
    return now - started > PROMPT_TIMEOUT
