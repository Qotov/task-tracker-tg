"""Drop every update whose sender is not one of the two allowed users.

Anyone can find a bot by its username, and this database holds document numbers,
so an unknown sender gets no reply at all — no error, no hint that the bot is
alive. The id is logged at warning level and the update is dropped before any
handler, filter or FSM state is touched.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject, Update, User

from bot.services.settings import bind_group

logger = logging.getLogger(__name__)

#: The command that moves the bot to a new group.
CLAIM_COMMAND = "/group"

#: Chat types that count as "the group".
_GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

#: Update fields that carry a sender, in the order Telegram documents them.
_SENDER_FIELDS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "message_reaction",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


def sender_of(event: TelegramObject) -> User | None:
    """The user behind an update, or None when there is none we can identify."""
    carrier = _carrier_of(event)
    user = getattr(carrier, "from_user", None)
    return user if isinstance(user, User) else None


def chat_of(event: TelegramObject) -> Chat | None:
    """The chat an update happened in, following a callback back to its message."""
    carrier = _carrier_of(event)
    chat = getattr(carrier, "chat", None)
    if chat is None:
        message = getattr(carrier, "message", None)
        chat = getattr(message, "chat", None)
    return chat if isinstance(chat, Chat) else None


def _is_claim_command(event: TelegramObject) -> bool:
    """The one thing a whitelisted person may say in a group we do not work in."""
    text = getattr(_carrier_of(event), "text", None) or ""
    return text.split("@")[0].strip().lower().startswith(CLAIM_COMMAND)


def _carrier_of(event: TelegramObject) -> TelegramObject | None:
    if not isinstance(event, Update):
        return event
    for field in _SENDER_FIELDS:
        carrier = getattr(event, field, None)
        if carrier is not None:
            return carrier if isinstance(carrier, TelegramObject) else None
    return None


class WhitelistMiddleware(BaseMiddleware):
    """Registered as an outer middleware on the update, so nothing runs before it."""

    def __init__(self, allowed_user_ids: Collection[int]) -> None:
        self.allowed_user_ids = frozenset(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = sender_of(event)
        if user is None:
            logger.warning("dropping update with no identifiable sender")
            return None
        if user.id not in self.allowed_user_ids:
            logger.warning("dropping update from non-whitelisted sender %s", user.id)
            return None
        if not self._group_is_ours(event, data):
            return None
        return await handler(event, data)

    def _group_is_ours(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        """One group only (section 6). The first one to speak to us is the one.

        A whitelisted user who adds the bot to another group gets silence there
        rather than a second, half-shared task list.
        """
        chat = chat_of(event)
        db = data.get("db")
        if chat is None or db is None or chat.type not in _GROUP_TYPES:
            return True
        if bind_group(db, chat.id):
            return True
        if _is_claim_command(event):
            # Otherwise /group could never be received in the group it is meant
            # to claim, and moving the bot would mean editing the database.
            return True
        logger.warning("dropping update from unclaimed group %s", chat.id)
        return False
