"""The pinned message in the group, edited in place (section 12).

Telegram limits how often a bot may write to a group, so edits are coalesced: a
handler sets a dirty flag and one long-lived task does at most one edit every few
seconds. Nothing calls `edit_message_text` from a handler. When the text has not
changed the edit is skipped entirely, because Telegram answers an identical edit
with an error that is not a failure.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot import render
from bot.config import Config
from bot.db import Database
from bot.services.settings import DASHBOARD_MESSAGE_ID, get_int, group_chat_id, set_int
from bot.services.stats import build_board
from bot.services.tasks import blocked_map, list_due_today
from bot.services.users import list_users

logger = logging.getLogger(__name__)

#: Never edit the pinned message more often than this (section 12).
DEBOUNCE_SECONDS = 5.0


def build_text(db: Database, *, now: datetime, config: Config) -> str:
    """What the pinned message says: today per owner, the counts, and what is next."""
    tasks = list_due_today(db, now=now, tz=config.tz)
    owners = {person.telegram_id: person for person in list_users(db)}
    board = build_board(db, now=now, tz=config.tz)
    return render.dashboard(
        tasks,
        owners,
        board=board,
        now=now,
        tz=config.tz,
        blocked=blocked_map(db, tasks),
    )


class Dashboard:
    """One pinned message, one asyncio task, one dirty flag."""

    def __init__(self, bot: Bot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self._dirty = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._last_text: str | None = None

    def touch(self) -> None:
        """Something changed. The edit itself happens later, and at most every 5 seconds."""
        self._dirty.set()

    def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._runner = None

    async def _loop(self) -> None:  # pragma: no cover - exercised by the bot, not the tests
        while True:
            await self._dirty.wait()
            await asyncio.sleep(DEBOUNCE_SECONDS)  # coalesce everything that arrived meanwhile
            self._dirty.clear()
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("could not refresh the dashboard")

    async def refresh(self) -> None:
        """Edit the pinned message, or post and pin a new one if it is gone."""
        chat_id = group_chat_id(self.db)
        if chat_id is None:
            return
        text = build_text(self.db, now=datetime.now(UTC), config=self.config)
        if text == self._last_text:
            return  # identical content: Telegram would answer with an error

        message_id = get_int(self.db, DASHBOARD_MESSAGE_ID)
        if message_id is not None:
            try:
                await self.bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
                self._last_text = text
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error):
                    self._last_text = text
                    return
                logger.info("the pinned dashboard is gone, posting a new one: %s", error)

        await self._post(chat_id, text)

    async def _post(self, chat_id: int, text: str) -> None:
        try:
            message = await self.bot.send_message(chat_id, text)
            await self.bot.pin_chat_message(chat_id, message.message_id, disable_notification=True)
        except TelegramAPIError:
            logger.exception("could not post the dashboard")
            return
        set_int(self.db, DASHBOARD_MESSAGE_ID, message.message_id)
        self._last_text = text


#: The one dashboard of this process, so handlers can mark it dirty without
#: threading it through every call. A single-process bot has exactly one.
_current: Dashboard | None = None


def set_current(dashboard: Dashboard | None) -> None:
    global _current  # noqa: PLW0603 - one process, one dashboard
    _current = dashboard


def touch() -> None:
    """Called wherever a task changes; does nothing when no dashboard is running."""
    if _current is not None:
        _current.touch()
