"""The whitelist must drop unknown senders before any handler sees them (section 15)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, Update
from aiogram.types import User as TelegramUser

from bot.config import Config
from bot.db import Database
from bot.main import build_dispatcher
from bot.middleware.whitelist import WhitelistMiddleware, sender_of
from tests.conftest import ROBIN_ID

#: Shaped like a Telegram token so aiogram accepts it; it is not one.
FAKE_TOKEN = "123456789:AAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
STRANGER_ID = 999_999_999


def _message(user_id: int, text: str = "buy milk") -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 9, 15, 8, 30, tzinfo=UTC),
        chat=Chat(id=-100_123, type="group"),
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Someone"),
        text=text,
    )


def _update(user_id: int, update_id: int = 1) -> Update:
    return Update(update_id=update_id, message=_message(user_id))


class Recorder:
    """Stands in for the rest of the middleware chain and the handlers behind it."""

    def __init__(self) -> None:
        self.seen: list[TelegramObject] = []

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> str:
        self.seen.append(event)
        return "handled"


def test_unknown_sender_is_dropped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder()
    middleware = WhitelistMiddleware({ROBIN_ID})

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(middleware(recorder, _update(STRANGER_ID), {}))

    assert result is None
    assert recorder.seen == []
    assert str(STRANGER_ID) in caplog.text


def test_allowed_sender_passes_through() -> None:
    recorder = Recorder()
    middleware = WhitelistMiddleware({ROBIN_ID})

    result = asyncio.run(middleware(recorder, _update(ROBIN_ID), {}))

    assert result == "handled"
    assert len(recorder.seen) == 1


def test_update_without_a_sender_is_dropped() -> None:
    recorder = Recorder()
    middleware = WhitelistMiddleware({ROBIN_ID})

    result = asyncio.run(middleware(recorder, Update(update_id=7), {}))

    assert result is None
    assert recorder.seen == []


def test_unknown_sender_never_reaches_a_handler() -> None:
    """The whole chain: only the whitelisted user's message arrives."""
    seen: list[int] = []
    router = Router(name="test")

    @router.message()
    async def catch_all(message: Message) -> None:
        assert message.from_user is not None
        seen.append(message.from_user.id)

    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(WhitelistMiddleware({ROBIN_ID}))
    dispatcher.include_router(router)

    async def scenario() -> None:
        bot = Bot(token=FAKE_TOKEN)
        try:
            await dispatcher.feed_update(bot, _update(STRANGER_ID, update_id=1))
            await dispatcher.feed_update(bot, _update(ROBIN_ID, update_id=2))
        finally:
            await bot.session.close()

    asyncio.run(scenario())

    assert seen == [ROBIN_ID]


def test_the_real_dispatcher_installs_the_whitelist(db: Database, tmp_path: Path) -> None:
    config = Config(
        bot_token=FAKE_TOKEN,
        allowed_user_ids=frozenset({ROBIN_ID}),
        db_path=tmp_path / "tasks.db",
        tz_name="Europe/Paris",
        gemini_api_key=None,
        gemini_model="gemini-3.5-flash-lite",
        backup_chat_id=None,
    )

    dispatcher = build_dispatcher(db, config)

    assert any(
        isinstance(middleware, WhitelistMiddleware)
        for middleware in dispatcher.update.outer_middleware
    )


def test_sender_of_reads_a_callback_query() -> None:
    user = TelegramUser(id=ROBIN_ID, is_bot=False, first_name="Robin")
    update = Update(
        update_id=3,
        callback_query=CallbackQuery(id="1", from_user=user, chat_instance="x", data="t:done:12"),
    )

    found = sender_of(update)

    assert found is not None
    assert found.id == ROBIN_ID


def test_sender_of_reads_a_bare_event() -> None:
    found = sender_of(_message(ROBIN_ID))

    assert found is not None
    assert found.id == ROBIN_ID


# --- who ends up in the users table ----------------------------------------

BOT_ID = 8_861_025_783


def test_a_bot_is_never_registered_as_a_user(db: Database) -> None:
    """Including this one: a bot cannot own a task."""
    from bot.handlers import register_person

    registered = register_person(
        TelegramUser(id=BOT_ID, is_bot=True, first_name="tasktrackerbot"),
        Chat(id=ROBIN_ID, type="private"),
        db,
    )

    assert registered is None
    assert db.query("SELECT * FROM users") == []


def test_pressing_a_button_registers_the_person_not_the_bot(db: Database) -> None:
    """`callback.message` was written by the bot, so its `from_user` is the bot."""
    from bot.handlers import register_presser

    card = Message(
        message_id=2,
        date=datetime(2026, 9, 15, 8, 30, tzinfo=UTC),
        chat=Chat(id=ROBIN_ID, type="private"),
        from_user=TelegramUser(id=BOT_ID, is_bot=True, first_name="tasktrackerbot"),
        text="#1 book the movers",
    )
    press = CallbackQuery(
        id="1",
        from_user=TelegramUser(id=ROBIN_ID, is_bot=False, first_name="Robin", username="robin"),
        chat_instance="x",
        message=card,
        data="t:done:1",
    )

    registered = register_presser(press, db)

    assert registered is not None
    assert registered.telegram_id == ROBIN_ID
    assert [row["telegram_id"] for row in db.query("SELECT telegram_id FROM users")] == [ROBIN_ID]
