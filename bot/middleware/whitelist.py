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
from aiogram.types import TelegramObject, Update, User

logger = logging.getLogger(__name__)

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
    if isinstance(event, Update):
        for field in _SENDER_FIELDS:
            carrier = getattr(event, field, None)
            if carrier is not None:
                user = getattr(carrier, "from_user", None)
                return user if isinstance(user, User) else None
        return None
    user = getattr(event, "from_user", None)
    return user if isinstance(user, User) else None


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
        return await handler(event, data)
