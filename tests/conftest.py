"""Shared fixtures: a real, empty database and the two people who use the bot."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from bot.config import Config
from bot.db import Database
from bot.parser import DEFAULT_TZ
from bot.services.users import User, ensure_user

#: A fixed clock for every test that cares: Tuesday 15 September 2026, 10:30 in Paris.
NOW = datetime(2026, 9, 15, 10, 30, tzinfo=DEFAULT_TZ)

ROBIN_ID = 111_111_111
SAM_ID = 222_222_222


@pytest.fixture
def db() -> Iterator[Database]:
    """An in-memory database with the real migrations applied."""
    database = Database.in_memory()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def robin(db: Database) -> User:
    return ensure_user(db, telegram_id=ROBIN_ID, username="robin", first_name="Robin")


@pytest.fixture
def sam(db: Database) -> User:
    return ensure_user(db, telegram_id=SAM_ID, username="sam", first_name="Sam")


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A configuration that never talks to Telegram; the token is a fake."""
    return Config(
        bot_token="123456789:AAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        allowed_user_ids=frozenset({ROBIN_ID, SAM_ID}),
        db_path=tmp_path / "tasks.db",
        tz_name="Europe/Paris",
        anthropic_api_key=None,
        anthropic_model="claude-haiku-4-5-20251001",
        backup_chat_id=None,
    )
