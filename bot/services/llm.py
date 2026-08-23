"""The optional second-chance parser (section 8), on Gemini.

The rule parser in `bot/parser.py` handles everything it recognises — owners,
projects and every date form — in microseconds, offline, and is what runs first.
This module is asked only when that parser found **no date at all** and the
message is long enough to be a sentence rather than a title. "we need to sort the
deposit out before the inspection at the end of next month" is the case it exists
for.

Three rules, from the spec and not negotiable:

* it never blocks task creation — the task is already saved before we ask;
* anything slower than four seconds is abandoned;
* any error at all falls back to what the rules produced.

Note what leaves the machine when this is switched on: the text of the task. With
no `GEMINI_API_KEY` set, nothing here ever runs and nothing is sent anywhere.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.parser import DEFAULT_DUE_HOUR, DEFAULT_DUE_MINUTE, DEFAULT_TZ, parse_when

logger = logging.getLogger(__name__)

#: Anything slower than this is not worth waiting for (section 8).
TIMEOUT_SECONDS = 4.0

#: Below this many words a message is a title, not a sentence worth interpreting.
MIN_WORDS = 8

#: How many subtasks we will accept from one answer.
MAX_SUBTASKS = 8

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: The shape we insist on. Gemini enforces it, so there is no markdown to strip.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "owner": {"type": "STRING"},
        "project": {"type": "STRING"},
        "due": {"type": "STRING"},
        "subtasks": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title"],
}

Transport = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Draft:
    """What the model made of the message. Every field is optional but the title."""

    title: str = ""
    owner: str | None = None
    project: str | None = None
    due_at: datetime | None = None
    subtasks: tuple[str, ...] = ()

    @property
    def is_useful(self) -> bool:
        """Only a date or subtasks are worth changing a saved task for."""
        return self.due_at is not None or bool(self.subtasks)


def should_ask(text: str, *, has_date: bool, config: Config) -> bool:
    """Section 8: no date found, more than eight words, and a key configured."""
    if has_date or not config.gemini_api_key:
        return False
    return len(text.split()) > MIN_WORDS


def build_prompt(text: str, *, now: datetime, tz: ZoneInfo, shorts: tuple[str, ...]) -> str:
    people = ", ".join(shorts) if shorts else "nobody yet"
    return (
        "Read this note from a shared task list and return what it is asking for.\n\n"
        f"Note: {text}\n\n"
        f"Right now it is {now.astimezone(tz):%Y-%m-%d %H:%M} ({tz}).\n"
        f"The people who can own a task are: {people}.\n\n"
        "Rules:\n"
        "- title: the task in a few words, imperative, no dates and no names.\n"
        "- due: local date and time as YYYY-MM-DDTHH:MM, or an empty string if the "
        "note really names no time. Never invent a deadline that is not implied.\n"
        "- owner: one of the people above, or an empty string.\n"
        "- project: one lowercase word grouping this work, or an empty string.\n"
        "- subtasks: the separate steps if the note clearly lists more than one, "
        "otherwise an empty list."
    )


async def read_task(
    text: str,
    *,
    config: Config,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    shorts: tuple[str, ...] = (),
    transport: Transport | None = None,
) -> Draft | None:
    """Ask the model. Returns None on any problem at all — the caller keeps the rules."""
    if not config.gemini_api_key:
        return None
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": build_prompt(text, now=now, tz=tz, shorts=shorts)}]}
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    url = ENDPOINT.format(model=config.gemini_model)
    send = transport or _post
    try:
        answer = await send(url, payload, config.gemini_api_key)
        return _to_draft(answer, now=now, tz=tz)
    except Exception:
        # A second-chance parser that can break task creation is worse than none.
        logger.warning("the model could not be reached or understood", exc_info=True)
        return None


async def _post(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """The only place this project talks to anything but Telegram."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(url, json=payload, headers={"x-goog-api-key": api_key}) as response,
    ):
        response.raise_for_status()
        body: dict[str, Any] = await response.json()
        return body


def _to_draft(answer: dict[str, Any], *, now: datetime, tz: ZoneInfo) -> Draft | None:
    """Dig the JSON out of the response envelope and sanity-check every field."""
    candidates = answer.get("candidates") or []
    parts = (candidates[0].get("content", {}) if candidates else {}).get("parts") or []
    raw = parts[0].get("text", "") if parts else ""
    if not raw:
        return None
    data = json.loads(raw)

    subtasks = tuple(
        str(item).strip() for item in (data.get("subtasks") or []) if str(item).strip()
    )[:MAX_SUBTASKS]

    return Draft(
        title=str(data.get("title") or "").strip(),
        owner=str(data.get("owner") or "").strip().lstrip("@").lower() or None,
        project=str(data.get("project") or "").strip().lstrip("#").lower() or None,
        due_at=_read_due(str(data.get("due") or ""), now=now, tz=tz),
        subtasks=subtasks,
    )


def _read_due(value: str, *, now: datetime, tz: ZoneInfo) -> datetime | None:
    """`2026-09-20T14:30`, `2026-09-20`, or anything the rule parser also knows."""
    text = value.strip()
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        return parse_when(text, now=now, tz=tz)
    if naive.tzinfo is None:
        if naive.time() == time(0, 0) and "T" not in text:
            naive = naive.replace(hour=DEFAULT_DUE_HOUR, minute=DEFAULT_DUE_MINUTE)
        naive = naive.replace(tzinfo=tz)
    # A model that hands back last year's date is more likely wrong than early.
    if naive < now - timedelta(days=1):
        return None
    return naive
