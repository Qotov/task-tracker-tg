"""Inline button callbacks, and the short dialogue that adds a subtask.

Section 13: every press changes the task, updates the card in place, and answers
the callback so the spinner on the button stops turning.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import answer_creation, refresh_card, register_sender
from bot.render import TaskAction
from bot.services import tasks as task_service
from bot.services.users import partner_of

logger = logging.getLogger(__name__)

router = Router(name="callbacks")

#: How long the "send me the subtask title" prompt stays valid (section 13).
SUBTASK_TIMEOUT = timedelta(minutes=5)


class SubtaskFlow(StatesGroup):
    waiting_for_title = State()


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

    if task_service.get_task(db, task_id) is None:
        await callback.answer(f"Task #{task_id} is gone.", show_alert=True)
        return

    if callback_data.action == "sub":
        await _start_subtask(callback, state, task_id=task_id)
        return

    task, toast = _apply(callback_data.action, db, task_id=task_id, now=now, config=config)
    if task is None:
        await callback.answer(toast, show_alert=True)
        return

    if isinstance(callback.message, Message):
        await refresh_card(callback.message, db, task, now=now, config=config)
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
    if action == "give":
        return _give_away(db, task_id=task_id)
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


# --- the subtask dialogue --------------------------------------------------


async def _start_subtask(callback: CallbackQuery, state: FSMContext, *, task_id: int) -> None:
    await state.set_state(SubtaskFlow.waiting_for_title)
    await state.update_data(parent_id=task_id, asked_at=datetime.now(UTC).isoformat())
    if isinstance(callback.message, Message):
        await callback.message.answer(render.SUBTASK_PROMPT.format(task_id=task_id))
    await callback.answer()


@router.message(SubtaskFlow.waiting_for_title, F.text)
async def on_subtask_title(
    message: Message, state: FSMContext, db: Database, config: Config
) -> None:
    """The next message becomes the subtask, then the state clears either way."""
    data = await state.get_data()
    await state.clear()

    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return
    text = (message.text or "").strip()
    if not text:
        return

    now = datetime.now(UTC)
    parent = task_service.get_task(db, int(data.get("parent_id", 0)))
    expired = _has_expired(data.get("asked_at"), now=now)

    if parent is None or expired:
        # Do not lose what they typed: it becomes an ordinary task instead.
        outcome = task_service.create_from_text(db, text, sender=user, now=now, tz=config.tz)
        await message.answer(render.SUBTASK_EXPIRED)
        await answer_creation(message, outcome, db, now=now, config=config)
        return

    outcome = task_service.create_from_text(
        db, text, sender=user, now=now, tz=config.tz, parent=parent
    )
    await answer_creation(message, outcome, db, now=now, config=config)


def _has_expired(asked_at: object, *, now: datetime) -> bool:
    if not isinstance(asked_at, str):  # pragma: no cover - always written with the state
        return True
    try:
        started = datetime.fromisoformat(asked_at)
    except ValueError:  # pragma: no cover - we wrote it ourselves
        return True
    return now - started > SUBTASK_TIMEOUT
