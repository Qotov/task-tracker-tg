"""Environment configuration.

Read once at startup and validated eagerly: a bot that cannot talk to Telegram, or
that does not know who is allowed to use it, must fail immediately with a message a
human can act on rather than half-start and misbehave later.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB_PATH = "tasks.db"
DEFAULT_TZ = "Europe/Paris"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"

#: Which country's public holidays to flag on a due date. `off` disables the whole
#: feature; only `FR` ships a table today.
DEFAULT_HOLIDAYS = "FR"
KNOWN_HOLIDAY_REGIONS = frozenset({"FR", "OFF"})

_HINT = (
    "Copy .env.example to .env, fill the values in, and start again "
    "(or export the variables in the environment)."
)


class ConfigError(RuntimeError):
    """The environment does not describe a runnable bot."""


@dataclass(frozen=True)
class Config:
    """Everything the bot needs from its environment."""

    bot_token: str
    allowed_user_ids: frozenset[int]
    db_path: Path
    tz_name: str
    gemini_api_key: str | None
    gemini_model: str
    backup_chat_id: int | None
    holidays: str = DEFAULT_HOLIDAYS

    @property
    def tz(self) -> ZoneInfo:
        """The display timezone. Storage is always UTC."""
        return ZoneInfo(self.tz_name)


def load_dotenv(path: Path) -> dict[str, str]:
    """Read a `KEY=value` file. Returns an empty mapping when the file is absent.

    Deliberately tiny: no interpolation, no `export` keyword, no multi-line values.
    Just enough to keep the token out of the shell history.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> Config:
    """Build a `Config` or raise `ConfigError` listing everything that is wrong.

    Values already present in `env` win over the ones in the dotenv file, so a
    systemd unit or a one-off shell export can override the file.
    """
    source: dict[str, str] = {}
    if dotenv_path is not None:
        source.update(load_dotenv(dotenv_path))
    source.update(os.environ if env is None else env)

    problems: list[str] = []

    token = source.get("BOT_TOKEN", "").strip()
    if not token:
        problems.append("BOT_TOKEN is missing. Get one from @BotFather in Telegram.")

    raw_ids = source.get("ALLOWED_USER_IDS", "").strip()
    allowed: set[int] = set()
    if not raw_ids:
        problems.append(
            "ALLOWED_USER_IDS is missing. Put both Telegram user ids in it, "
            "comma-separated, for example 111111111,222222222."
        )
    else:
        for chunk in raw_ids.split(","):
            piece = chunk.strip()
            if not piece:
                continue
            try:
                allowed.add(int(piece))
            except ValueError:
                problems.append(f"ALLOWED_USER_IDS contains {piece!r}, which is not a number.")
        if not allowed and not problems:
            problems.append("ALLOWED_USER_IDS is empty after parsing.")

    db_path = Path(source.get("DB_PATH", "").strip() or DEFAULT_DB_PATH)

    tz_name = source.get("TZ", "").strip() or DEFAULT_TZ
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"TZ is {tz_name!r}, which is not a known timezone name.")

    holidays = (source.get("HOLIDAYS", "").strip() or DEFAULT_HOLIDAYS).upper()
    if holidays not in KNOWN_HOLIDAY_REGIONS:
        problems.append(
            f"HOLIDAYS is {holidays!r}; the ones I know are "
            + ", ".join(sorted(KNOWN_HOLIDAY_REGIONS))
            + "."
        )

    backup_raw = source.get("BACKUP_CHAT_ID", "").strip()
    backup_chat_id: int | None = None
    if backup_raw:
        try:
            backup_chat_id = int(backup_raw)
        except ValueError:
            problems.append(f"BACKUP_CHAT_ID is {backup_raw!r}, which is not a number.")

    if problems:
        raise ConfigError(
            "Cannot start the bot:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + f"\n{_HINT}"
        )

    return Config(
        bot_token=token,
        allowed_user_ids=frozenset(allowed),
        db_path=db_path,
        tz_name=tz_name,
        gemini_api_key=source.get("GEMINI_API_KEY", "").strip() or None,
        gemini_model=source.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
        backup_chat_id=backup_chat_id,
        holidays=holidays,
    )
