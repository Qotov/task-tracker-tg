"""Configuration must fail loudly and specifically, before the bot does anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import DEFAULT_GEMINI_MODEL, ConfigError, load_config, load_dotenv

VALID = {"BOT_TOKEN": "123456789:token", "ALLOWED_USER_IDS": "111,222"}


def test_a_complete_environment_loads() -> None:
    config = load_config({**VALID, "DB_PATH": "/data/tasks.db", "TZ": "Europe/Paris"})

    assert config.bot_token == "123456789:token"
    assert config.allowed_user_ids == frozenset({111, 222})
    assert config.db_path == Path("/data/tasks.db")
    assert config.tz.key == "Europe/Paris"


def test_defaults_are_filled_in() -> None:
    config = load_config(VALID)

    assert config.db_path == Path("tasks.db")
    assert config.tz_name == "Europe/Paris"
    assert config.gemini_model == DEFAULT_GEMINI_MODEL
    assert config.gemini_api_key is None
    assert config.backup_chat_id is None


def test_missing_token_is_named_in_the_error() -> None:
    with pytest.raises(ConfigError) as error:
        load_config({"ALLOWED_USER_IDS": "111"})

    assert "BOT_TOKEN" in str(error.value)
    assert "BotFather" in str(error.value)


def test_missing_users_is_named_in_the_error() -> None:
    with pytest.raises(ConfigError) as error:
        load_config({"BOT_TOKEN": "123:token"})

    assert "ALLOWED_USER_IDS" in str(error.value)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ConfigError) as error:
        load_config({})

    message = str(error.value)
    assert "BOT_TOKEN" in message
    assert "ALLOWED_USER_IDS" in message


def test_non_numeric_user_id_is_rejected() -> None:
    with pytest.raises(ConfigError, match="not a number"):
        load_config({**VALID, "ALLOWED_USER_IDS": "111,robin"})


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ConfigError, match="not a known timezone"):
        load_config({**VALID, "TZ": "Mars/Olympus"})


def test_optional_values_are_read() -> None:
    config = load_config(
        {**VALID, "GEMINI_API_KEY": "sk-test", "GEMINI_MODEL": "x", "BACKUP_CHAT_ID": "-42"}
    )

    assert config.gemini_api_key == "sk-test"
    assert config.gemini_model == "x"
    assert config.backup_chat_id == -42


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n\nBOT_TOKEN='123:from-file'\nALLOWED_USER_IDS=\"111,222\"\n",
        encoding="utf-8",
    )

    values = load_dotenv(env_file)

    assert values == {"BOT_TOKEN": "123:from-file", "ALLOWED_USER_IDS": "111,222"}


def test_missing_dotenv_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_environment_wins_over_the_dotenv_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BOT_TOKEN=123:from-file\nALLOWED_USER_IDS=111\n", encoding="utf-8")

    config = load_config({"BOT_TOKEN": "123:from-env"}, dotenv_path=env_file)

    assert config.bot_token == "123:from-env"
    assert config.allowed_user_ids == frozenset({111})
