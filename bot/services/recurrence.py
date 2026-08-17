"""Reading the `recurrence` column, and working out when a task comes back.

The four shapes the schema allows (section 5): `daily`, `weekly:mon`,
`monthly:15`, `yearly:09-20`. Everything here is pure date arithmetic on local
dates — the caller keeps the time of day.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_SPEC = re.compile(r"^(daily|weekly|monthly|yearly)(?::(.+))?$", re.IGNORECASE)
_MONTH_DAY = re.compile(r"^(\d{1,2})-(\d{1,2})$")


@dataclass(frozen=True)
class Recurrence:
    kind: str
    detail: str | None = None

    def stored(self) -> str:
        return self.kind if self.detail is None else f"{self.kind}:{self.detail}"

    def describe(self) -> str:
        """How a card says it: "every Monday", "on the 15th"."""
        if self.kind == "daily":
            return "every day"
        if self.kind == "weekly":
            if self.detail is None:
                return "every week"
            name = {
                "mon": "Monday",
                "tue": "Tuesday",
                "wed": "Wednesday",
                "thu": "Thursday",
                "fri": "Friday",
                "sat": "Saturday",
                "sun": "Sunday",
            }[self.detail]
            return f"every {name}"
        if self.kind == "monthly":
            return "every month" if self.detail is None else f"on the {self.detail}th"
        if self.detail is None:  # pragma: no cover - yearly always carries a date
            return "every year"
        month, day = self.detail.split("-")
        return f"every {int(day)}/{int(month)}"


def parse_recurrence(spec: str) -> Recurrence | None:
    """Read `weekly:mon` and friends. Returns None when it is not one of the four."""
    match = _SPEC.match(spec.strip().lower())
    if match is None:
        return None
    kind, detail = match.group(1), match.group(2)

    if kind == "daily":
        return Recurrence("daily") if detail is None else None
    if kind == "weekly":
        if detail is None:
            return Recurrence("weekly")
        return Recurrence("weekly", detail[:3]) if detail[:3] in WEEKDAYS else None
    if kind == "monthly":
        if detail is None:
            return Recurrence("monthly")
        return (
            Recurrence("monthly", detail) if detail.isdigit() and 1 <= int(detail) <= 31 else None
        )
    if detail is None:
        return Recurrence("yearly")
    parts = _MONTH_DAY.match(detail)
    if parts is None:
        return None
    month, day = int(parts.group(1)), int(parts.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return Recurrence("yearly", f"{month:02d}-{day:02d}")


def next_due(rule: Recurrence, *, after: date) -> date:
    """The first date this rule lands on strictly after `after`."""
    if rule.kind == "daily":
        return after + timedelta(days=1)

    if rule.kind == "weekly":
        if rule.detail is None:
            return after + timedelta(days=7)
        ahead = (WEEKDAYS[rule.detail] - after.weekday()) % 7
        return after + timedelta(days=ahead or 7)

    if rule.kind == "monthly":
        day = int(rule.detail) if rule.detail else after.day
        candidate = _clamped(after.year, after.month, day)
        if candidate > after:
            return candidate
        year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
        return _clamped(year, month, day)

    month, day = (
        int(part) for part in (rule.detail or f"{after.month:02d}-{after.day:02d}").split("-")
    )
    candidate = _clamped(after.year, month, day)
    return candidate if candidate > after else _clamped(after.year + 1, month, day)


def _clamped(year: int, month: int, day: int) -> date:
    """The 31st of a 30-day month means its last day, not the 1st of the next."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))
