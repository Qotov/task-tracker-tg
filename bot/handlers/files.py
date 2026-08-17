"""Document and photo intake (section 14).

A scan arrives, the bot asks which task it belongs to, and the answer is one tap.
Only the `file_id` is stored — Telegram keeps the file itself, so no document
number ever lands on the server's disk.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers import register_presser, register_sender
from bot.render import DOC_SEARCH, DocAction
from bot.services import docs as doc_service
from bot.services.tasks import get_task

logger = logging.getLogger(__name__)

router = Router(name="files")


@router.message(F.document | F.photo)
async def on_file(message: Message, db: Database, config: Config) -> None:
    """Take the file in first, ask where it belongs second: never lose the scan."""
    user = register_sender(message, db)
    if user is None:  # pragma: no cover - the whitelist guarantees a sender
        return

    now = datetime.now(UTC)
    if message.document is not None:
        stored = doc_service.store(
            db,
            file_id=message.document.file_id,
            file_unique_id=message.document.file_unique_id,
            file_name=message.document.file_name,
            mime=message.document.mime_type,
            kind="document",
            caption=message.caption,
            added_by=user.telegram_id,
            added_at=now,
        )
    elif message.photo:
        largest = message.photo[-1]  # Telegram sends every size; keep the biggest
        stored = doc_service.store(
            db,
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            kind="photo",
            caption=message.caption,
            added_by=user.telegram_id,
            added_at=now,
        )
    else:  # pragma: no cover - the filter guarantees one or the other
        return

    await message.answer(
        render.intake_text(stored),
        reply_markup=render.intake_keyboard(stored, doc_service.recently_touched(db)),
    )


@router.callback_query(DocAction.filter())
async def on_intake_button(
    callback: CallbackQuery,
    callback_data: DocAction,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    """Attach it to a task, keep it loose, or go looking for the right one."""
    user = register_presser(callback, db)
    if user is None:  # pragma: no cover - whitelisted humans only
        await callback.answer()
        return

    if callback_data.task_id == DOC_SEARCH:
        await _ask_for_query(callback, state, attachment_id=callback_data.attachment_id)
        return

    task_id = callback_data.task_id or None
    attachment = doc_service.attach_to(db, callback_data.attachment_id, task_id)
    if attachment is None:
        await callback.answer("That file is gone.", show_alert=True)
        return

    task = None if task_id is None else get_task(db, task_id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(render.filed_text(attachment, task))
    await callback.answer("Filed" if task_id else "Kept")


async def _ask_for_query(callback: CallbackQuery, state: FSMContext, *, attachment_id: int) -> None:
    from bot.handlers.callbacks import TaskInput

    await state.set_state(TaskInput.waiting_for_text)
    await state.update_data(
        task_id=attachment_id, kind="attach_search", asked_at=datetime.now(UTC).isoformat()
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(render.DOC_SEARCH_PROMPT)
    await callback.answer()
