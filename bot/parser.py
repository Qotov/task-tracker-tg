"""Rule-based free-text task parsing (section 8 of docs/TASK.md).

Pure functions: no database, no Telegram, no clock of its own. Everything the
parser needs is passed in, which is what makes `tests/test_parser.py` a plain
table of inputs and expected outputs.

Markers, all stripped from the resulting title:

* ``@me`` / ``@<short>`` — the owner. ``@us`` is refused: a task has one owner.
* ``#<word>`` — the project.
* ``!<word>`` — ignored, with a warning. There is no priority in this bot.
* a date, optionally followed by a time (``tomorrow 14:30``, ``20/09``, ``+3d``).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

#: A date without a time means 09:00 in the display timezone.
DEFAULT_DUE_HOUR = 9
DEFAULT_DUE_MINUTE = 0

ONE_OWNER_ERROR = (
    "A task has exactly one owner, so <b>@us</b> does not work. "
    "Use <b>@me</b> or <b>@name</b>, or add two tasks."
)
NO_TITLE_ERROR = "I could not find a title in that message."
PRIORITY_WARNING = (
    "Priorities are not supported — the due date carries the urgency. I ignored <b>{marker}</b>."
)

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_OWNER_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{1,32})")
_US_RE = re.compile(r"(?<![\w@])@us(?!\w)", re.IGNORECASE)
_PROJECT_RE = re.compile(r"(?<![\w#])#([A-Za-z0-9_][A-Za-z0-9_-]{0,39})")
_PRIORITY_RE = re.compile(r"(?<![\w!])!([A-Za-z0-9_][A-Za-z0-9_-]{0,19})")

_WEEKDAY_ALTERNATION = (
    "monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun"
)
_WHEN_RE = re.compile(
    r"(?<![\w+])(?:(?:at|on|by)\s+)?"
    r"(?:"
    r"(?:"
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<slash>\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"|(?P<dot>\d{1,2}\.\d{2}(?:\.\d{2,4})?)"
    r"|(?P<rel>\+\d{1,3}[dwm])"
    r"|(?P<word>today|tomorrow)"
    rf"|(?P<weekday>{_WEEKDAY_ALTERNATION})"
    r")"
    r"(?:[\s,]+(?:at\s+)?(?P<dhour>\d{1,2}):(?P<dminute>\d{2}))?"
    r"|(?P<bhour>\d{1,2}):(?P<bminute>\d{2})"
    r")(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedTask:
    """What a free-text message asked for. `owner` and `project` are already resolved."""

    title: str
    owner: str
    project: str | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_task(
    text: str,
    *,
    now: datetime,
    sender_short: str,
    known_shorts: Iterable[str] = (),
    default_project: str | None = None,
    default_owner: str | None = None,
    tz: ZoneInfo = PARIS,
) -> ParsedTask:
    """Turn a raw message into a task draft.

    `known_shorts` are the registered user shorts; an `@word` that is not one of
    them (and is not `@me`) is left in the title, because it is a mention of some
    third person rather than an owner.

    `default_owner` is the owner when the text names none — a subtask inherits its
    parent's owner this way. `@me` always means the sender, whatever the default is.
    """
    if now.tzinfo is None:
        raise ValueError("parse_task needs an aware datetime for `now`")
    local_now = now.astimezone(tz)
    shorts = {short.lower() for short in known_shorts}

    working = text.replace(" ", " ").strip()
    warnings: list[str] = []

    if _US_RE.search(working):
        return ParsedTask(title=_tidy(working), owner=sender_short, error=ONE_OWNER_ERROR)

    working, named_owner = _extract_owner(working, sender_short=sender_short, shorts=shorts)
    owner = named_owner or default_owner or sender_short
    working, project = _extract_project(working)
    working, priority_marker = _extract_priority(working)
    if priority_marker is not None:
        warnings.append(PRIORITY_WARNING.format(marker=priority_marker))

    working, due_at = _extract_due(working, local_now=local_now, tz=tz)

    title = _tidy(working)
    if not title:
        return ParsedTask(
            title="",
            owner=owner,
            project=project or default_project,
            due_at=due_at,
            remind_at=due_at,
            warnings=tuple(warnings),
            error=NO_TITLE_ERROR,
        )

    return ParsedTask(
        title=title,
        owner=owner,
        project=project or default_project,
        due_at=due_at,
        remind_at=due_at,
        warnings=tuple(warnings),
    )


def parse_when(text: str, *, now: datetime, tz: ZoneInfo = PARIS) -> datetime | None:
    """Read a date expression on its own, for `/due 20/09` and friends.

    Same forms as inside a task message; returns the UTC moment, or None when the
    text holds no date this parser recognises.
    """
    if now.tzinfo is None:
        raise ValueError("parse_when needs an aware datetime for `now`")
    _, due_at = _extract_due(text, local_now=now.astimezone(tz), tz=tz)
    return due_at


def parse_task_ref(raw: str) -> int | None:
    """Read a task id argument. Both `12` and `#12` are accepted."""
    candidate = raw.strip().lstrip("#").strip()
    if not candidate.isdigit():
        return None
    return int(candidate)


# --- markers ---------------------------------------------------------------


def _extract_owner(text: str, *, sender_short: str, shorts: set[str]) -> tuple[str, str | None]:
    owner: str | None = None
    spans: list[tuple[int, int]] = []
    for match in _OWNER_RE.finditer(text):
        handle = match.group(1).lower()
        if handle == "me":
            resolved = sender_short
        elif handle in shorts:
            resolved = handle
        else:
            continue  # a mention of somebody who is not a user of this bot
        if owner is None:
            owner = resolved
        spans.append(match.span())
    return _remove_spans(text, spans), owner


def _extract_project(text: str) -> tuple[str, str | None]:
    project: str | None = None
    spans: list[tuple[int, int]] = []
    for match in _PROJECT_RE.finditer(text):
        if project is None:
            project = match.group(1).lower()
        spans.append(match.span())
    return _remove_spans(text, spans), project


def _extract_priority(text: str) -> tuple[str, str | None]:
    marker: str | None = None
    spans: list[tuple[int, int]] = []
    for match in _PRIORITY_RE.finditer(text):
        if marker is None:
            marker = match.group(0)
        spans.append(match.span())
    return _remove_spans(text, spans), marker


# --- dates -----------------------------------------------------------------


def _extract_due(text: str, *, local_now: datetime, tz: ZoneInfo) -> tuple[str, datetime | None]:
    """Find the first usable date and/or time, remove it, and return the UTC due moment."""
    today = local_now.date()
    date_match: re.Match[str] | None = None
    date_value: date | None = None
    time_match: re.Match[str] | None = None

    for match in _WHEN_RE.finditer(text):
        if match.group("bhour") is not None:
            if time_match is None and _valid_time(match.group("bhour"), match.group("bminute")):
                time_match = match
            continue
        if date_match is not None:
            continue
        if match.group("dhour") is not None and not _valid_time(
            match.group("dhour"), match.group("dminute")
        ):
            continue
        resolved = _resolve_date(match, today)
        if resolved is not None:
            date_match, date_value = match, resolved

    spans: list[tuple[int, int]] = []
    if date_match is not None and date_value is not None:
        spans.append(date_match.span())
        day = date_value
        if date_match.group("dhour") is not None:
            hour, minute = int(date_match.group("dhour")), int(date_match.group("dminute"))
        elif time_match is not None:
            # "call the mairie at 14:30 tomorrow" — one date, one time, both meant.
            hour, minute = int(time_match.group("bhour")), int(time_match.group("bminute"))
            spans.append(time_match.span())
        else:
            hour, minute = DEFAULT_DUE_HOUR, DEFAULT_DUE_MINUTE
    elif time_match is not None:
        spans.append(time_match.span())
        day = today
        hour, minute = int(time_match.group("bhour")), int(time_match.group("bminute"))
    else:
        return text, None

    due_local = datetime.combine(day, time(hour, minute), tzinfo=tz)
    return _remove_spans(text, spans), due_local.astimezone(UTC)


def _valid_time(hour: str, minute: str) -> bool:
    return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59


def _resolve_date(match: re.Match[str], today: date) -> date | None:
    """The calendar date a matched expression points at, or None when it is nonsense."""
    iso = match.group("iso")
    if iso is not None:
        try:
            return date.fromisoformat(iso)
        except ValueError:
            return None

    numeric = match.group("slash") or match.group("dot")
    if numeric is not None:
        parts = re.split(r"[./]", numeric)
        day, month = int(parts[0]), int(parts[1])
        if len(parts) == 3:
            year = int(parts[2])
            return _safe_date(year + 2000 if year < 100 else year, month, day)
        candidate = _safe_date(today.year, month, day)
        if candidate is None:
            return None
        # A bare day/month that has already passed means the one coming up.
        return candidate if candidate >= today else _safe_date(today.year + 1, month, day)

    relative = match.group("rel")
    if relative is not None:
        amount, unit = int(relative[1:-1]), relative[-1].lower()
        if unit == "d":
            return today + timedelta(days=amount)
        if unit == "w":
            return today + timedelta(weeks=amount)
        return _add_months(today, amount)

    word = match.group("word")
    if word is not None:
        return today if word.lower() == "today" else today + timedelta(days=1)

    weekday = match.group("weekday")
    if weekday is not None:
        target = _WEEKDAYS[weekday[:3].lower()]
        ahead = (target - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)

    return None  # pragma: no cover - the regex has no other branch


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_months(start: date, months: int) -> date:
    """Add whole months, clamping to the end of the shorter month (31 Jan + 1m = 28 Feb)."""
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


# --- text ------------------------------------------------------------------


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(" ,;:").strip()
