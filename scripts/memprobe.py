"""Measure the bot's resident memory under a realistic and then an absurd load.

Not a test: a measuring stick. It builds the real Dispatcher, the real scheduler
and a real on-disk database, then asks how much RSS the process actually holds —
so a container memory limit can be chosen from evidence rather than from nerves.

    uv run python scripts/memprobe.py

Nothing here talks to Telegram: the Bot object is constructed with a
well-formed but fake token and no request is ever made.
"""

from __future__ import annotations

import gc
import os
import resource
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.db import MIGRATIONS_DIR, Database  # noqa: E402
from bot.main import build_dispatcher  # noqa: E402
from bot.scheduler import build_scheduler  # noqa: E402
from bot.services import tasks as task_service  # noqa: E402

FAKE_TOKEN = "123456789:AAEbCdEfGhIjKlMnOpQrStUvWxYz0123456"  # noqa: S105 - never sent


def rss_mb() -> float:
    """Peak resident set size in MB. ru_maxrss is bytes on macOS, KB on Linux."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def current_rss_mb() -> float:
    """Live RSS, which unlike ru_maxrss can go down again."""
    try:
        with open(f"/proc/{os.getpid()}/statm") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except OSError:
        import subprocess

        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
        )
        return int(out.stdout.strip()) / 1024


def report(label: str) -> None:
    gc.collect()
    print(f"  {label:<44} {current_rss_mb():7.1f} MB live   {rss_mb():7.1f} MB peak")


def main() -> int:
    print("\n=== resident memory, building the real bot ===\n")
    report("interpreter + imports")

    tmp = Path(tempfile.mkdtemp()) / "probe.db"
    config = Config(
        bot_token=FAKE_TOKEN,
        allowed_user_ids=frozenset({1, 2}),
        db_path=tmp,
        tz_name="Europe/Paris",
        gemini_api_key=None,
        gemini_model="gemini-3.5-flash-lite",
        backup_chat_id=None,
    )

    db = Database.connect(tmp)
    db.migrate(MIGRATIONS_DIR)
    for telegram_id, short in ((1, "alpha"), (2, "beta")):
        db.execute(
            "INSERT INTO users (telegram_id, short, display_name, dm_chat_id) VALUES (?,?,?,?)",
            (telegram_id, short, short.title(), telegram_id),
        )
    report("database + migrations")

    bot = Bot(token=FAKE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = build_dispatcher(db, config)
    scheduler = build_scheduler(bot, db, config)
    report("Bot + Dispatcher + scheduler (idle)")

    print(f"\n  scheduler jobs registered: {len(scheduler.get_jobs())}")
    for job in scheduler.get_jobs():
        print(f"    - {job.id}: {job.trigger}")
    print()

    now = datetime.now(UTC)
    for count in (100, 1_000, 10_000):
        while _task_count(db) < count:
            n = _task_count(db)
            task_service.create_task(
                db,
                title=f"task number {n} with a title of a plausibly realistic length",
                owner_id=1 if n % 2 else 2,
                created_by=1,
                now=now,
                due_at=now + timedelta(days=n % 90),
                project="probe" if n % 3 else None,
            )
        report(f"{count:,} tasks in the database")

    print()
    for _ in range(200):
        list(db.execute("SELECT * FROM tasks WHERE status = 'todo'").fetchall())
    report("200 full table scans (the tick's worst case)")

    del dispatcher, scheduler, bot
    gc.collect()
    report("after dropping the dispatcher")

    print(f"\n  peak across the whole run: {rss_mb():.1f} MB")
    print("  a 256mb container leaves "
          f"{256 - rss_mb():.0f} MB of headroom at 10,000 tasks\n")
    return 0


def _task_count(db: Database) -> int:
    row = db.execute("SELECT count(*) AS n FROM tasks").fetchone()
    return int(row["n"])


if __name__ == "__main__":
    raise SystemExit(main())
