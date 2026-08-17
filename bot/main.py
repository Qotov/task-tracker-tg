"""Entry point: `uv run python -m bot.main` (or `make run`).

Reads the configuration, applies migrations, wires the whitelist middleware in
front of every update, and starts long polling. The scheduler joins in Phase 3.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Config, ConfigError, load_config
from bot.db import MIGRATIONS_DIR, Database
from bot.handlers import callbacks, commands, freeform
from bot.middleware.whitelist import WhitelistMiddleware

logger = logging.getLogger("bot")

DOTENV_PATH = Path(".env")


def build_dispatcher(db: Database, config: Config) -> Dispatcher:
    """A Dispatcher with the whitelist in front and `db`/`config` injected into handlers.

    Router order matters: commands first, then the callbacks router which owns the
    subtask dialogue, and only then the catch-all that turns plain text into a task.
    The FSM lives in memory, so a restart forgets a half-finished subtask prompt.
    """
    dispatcher = Dispatcher(storage=MemoryStorage(), db=db, config=config)
    dispatcher.update.outer_middleware(WhitelistMiddleware(config.allowed_user_ids))
    dispatcher.include_router(commands.router)
    dispatcher.include_router(callbacks.router)
    dispatcher.include_router(freeform.router)
    return dispatcher


async def run(config: Config) -> None:
    db = Database.connect(config.db_path)
    applied = db.migrate(MIGRATIONS_DIR)
    if applied:
        logger.info("applied migrations: %s", ", ".join(str(number) for number in applied))

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(db, config)
    logger.info("polling as a bot for %d allowed user(s)", len(config.allowed_user_ids))
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        db.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        config = load_config(dotenv_path=DOTENV_PATH)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:  # pragma: no cover - Ctrl-C during long polling
        logger.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
