"""French public holidays — the days the mairie, the préfecture and the CAF are shut.

Half of what this bot tracks is an appointment with a French office, so a due date
that lands on a `jour férié` is worth saying out loud. Computed rather than
fetched: the rules have not changed since 1953 and a bot that needs the network to
render a card is a bot that breaks on a train.
"""

from __future__ import annotations

from datetime import date, timedelta

#: Fixed-date holidays, (month, day) -> name.
_FIXED = {
    (1, 1): "Jour de l'an",
    (5, 1): "Fête du Travail",
    (5, 8): "Victoire 1945",
    (7, 14): "Fête nationale",
    (8, 15): "Assomption",
    (11, 1): "Toussaint",
    (11, 11): "Armistice 1918",
    (12, 25): "Noël",
}

#: Holidays that move with Easter, name -> days after Easter Sunday.
_MOVEABLE = {
    "Lundi de Pâques": 1,
    "Ascension": 39,
    "Lundi de Pentecôte": 50,
}


def french_holiday(day: date) -> str | None:
    """The name of the public holiday on that date, or None on a working day."""
    fixed = _FIXED.get((day.month, day.day))
    if fixed is not None:
        return fixed
    sunday = easter_sunday(day.year)
    for name, offset in _MOVEABLE.items():
        if day == sunday + timedelta(days=offset):
            return name
    return None


def easter_sunday(year: int) -> date:
    """Gregorian Easter, by the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, dayofmonth = divmod(h + lunar - 7 * m + 114, 31)
    return date(year, month, dayofmonth + 1)
