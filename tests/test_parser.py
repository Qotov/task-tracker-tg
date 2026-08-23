"""The parser table — the most important test file in the repo (section 8).

`NOW` is Tuesday 15 September 2026, 10:30 Paris. The sender is `robin`, and the
registered users are `robin` and `sam`. Expected due dates are written as Paris
local time, because that is how a human reads them; storage is UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from bot.parser import (
    DEFAULT_TZ,
    NO_TITLE_ERROR,
    ONE_OWNER_ERROR,
    ParsedTask,
    parse_task,
    parse_task_ref,
)
from tests.conftest import NOW

KNOWN_SHORTS = ("robin", "sam")


@dataclass(frozen=True)
class Case:
    text: str
    title: str
    owner: str = "robin"
    project: str | None = None
    due: str | None = None  # "YYYY-MM-DD HH:MM" in Paris local time


CASES = [
    # --- no markers at all --------------------------------------------------
    Case("buy milk", title="buy milk"),
    Case("  spaced   out   task  ", title="spaced out task"),
    Case("buy 1.5 kg of flour", title="buy 1.5 kg of flour"),
    Case("mail the notice to a@b.com", title="mail the notice to a@b.com"),
    # --- owner --------------------------------------------------------------
    Case("@sam call the landlord", title="call the landlord", owner="sam"),
    Case("@me pick up the keys", title="pick up the keys", owner="robin"),
    Case("@SAM shout about the deposit", title="shout about the deposit", owner="sam"),
    Case("ping @stranger about the lease", title="ping @stranger about the lease"),
    # --- project ------------------------------------------------------------
    Case("#move book the movers", title="book the movers", project="move"),
    Case("book the movers #move", title="book the movers", project="move"),
    Case("#MOVE pack the boxes", title="pack the boxes", project="move"),
    Case("#paperwork collect the payslips", title="collect the payslips", project="paperwork"),
    # --- named days ---------------------------------------------------------
    Case("call the mairie today", title="call the mairie", due="2026-09-15 09:00"),
    Case("call the mairie tomorrow", title="call the mairie", due="2026-09-16 09:00"),
    Case("gym mon", title="gym", due="2026-09-21 09:00"),
    Case("gym tue", title="gym", due="2026-09-22 09:00"),  # today is a Tuesday: next week
    Case("gym sun", title="gym", due="2026-09-20 09:00"),
    Case("gym monday", title="gym", due="2026-09-21 09:00"),
    # --- numeric dates ------------------------------------------------------
    Case("dentist 20/09", title="dentist", due="2026-09-20 09:00"),
    Case("dentist 20.09", title="dentist", due="2026-09-20 09:00"),
    Case("dentist 2026-09-20", title="dentist", due="2026-09-20 09:00"),
    Case("dentist 20/09/2026", title="dentist", due="2026-09-20 09:00"),
    Case("renew the carte vitale 01/03", title="renew the carte vitale", due="2027-03-01 09:00"),
    Case("renew insurance 2026-12-31", title="renew insurance", due="2026-12-31 09:00"),
    # --- month names, because people write them ------------------------------
    Case("book the movers Sep 24", title="book the movers", due="2026-09-24 09:00"),
    Case("call the mairie 24 Sep", title="call the mairie", due="2026-09-24 09:00"),
    Case("dentist September 24", title="dentist", due="2026-09-24 09:00"),
    Case("dentist 24 September 14:30", title="dentist", due="2026-09-24 14:30"),
    Case("pay the timbre 24 sep 2027", title="pay the timbre", due="2027-09-24 09:00"),
    Case("renew the carte 1 Mar", title="renew the carte", due="2027-03-01 09:00"),
    # a month name on its own is just a word
    Case("march to the mairie", title="march to the mairie"),
    Case("the movers may come", title="the movers may come"),
    # --- relative dates -----------------------------------------------------
    Case("send the scans +3d", title="send the scans", due="2026-09-18 09:00"),
    Case("send the scans +2w", title="send the scans", due="2026-09-29 09:00"),
    Case("pay the rent +1m", title="pay the rent", due="2026-10-15 09:00"),
    Case("+1m renew the lease", title="renew the lease", due="2026-10-15 09:00"),
    # --- times --------------------------------------------------------------
    Case("standup 14:30", title="standup", due="2026-09-15 14:30"),
    Case("call the bank tomorrow 14:30", title="call the bank", due="2026-09-16 14:30"),
    Case("call the bank tomorrow at 14:30", title="call the bank", due="2026-09-16 14:30"),
    Case("call the mairie at 14:30 tomorrow", title="call the mairie", due="2026-09-16 14:30"),
    Case("meeting on 20/09 at 09:30", title="meeting", due="2026-09-20 09:30"),
    Case("inspection 2026-09-20 18:00", title="inspection", due="2026-09-20 18:00"),
    Case("call mum sat 18:00", title="call mum", due="2026-09-19 18:00"),
    # --- several markers at once -------------------------------------------
    Case(
        "@sam #move call the movers tomorrow 10:00",
        title="call the movers",
        owner="sam",
        project="move",
        due="2026-09-16 10:00",
    ),
    Case(
        "deposit paperwork @me #move fri",
        title="deposit paperwork",
        owner="robin",
        project="move",
        due="2026-09-18 09:00",
    ),
    # --- priorities are stripped, never stored -----------------------------
    Case("!urgent fix the boiler", title="fix the boiler"),
    Case("fix the boiler !high tomorrow", title="fix the boiler", due="2026-09-16 09:00"),
    # --- things that only look like dates ----------------------------------
    Case("30/02 is not a real date", title="30/02 is not a real date"),
    Case("meeting at 25:00", title="meeting at 25:00"),
    Case(
        "review the lease 20/09 and again 21/09",
        title="review the lease and again 21/09",
        due="2026-09-20 09:00",
    ),
]


def _parse(text: str, **kwargs: object) -> ParsedTask:
    return parse_task(
        text,
        now=NOW,
        sender_short="robin",
        known_shorts=KNOWN_SHORTS,
        **kwargs,  # type: ignore[arg-type]
    )


def _local(due: datetime | None) -> str | None:
    return None if due is None else due.astimezone(DEFAULT_TZ).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize("case", CASES, ids=[case.text.strip() for case in CASES])
def test_parses(case: Case) -> None:
    result = _parse(case.text)

    assert result.error is None
    assert result.title == case.title
    assert result.owner == case.owner
    assert result.project == case.project
    assert _local(result.due_at) == case.due


def test_case_table_is_big_enough() -> None:
    """Section 8 asks for at least 30 cases; keep it that way."""
    assert len(CASES) >= 30


def test_remind_at_matches_due_at() -> None:
    result = _parse("call the mairie tomorrow")

    assert result.remind_at == result.due_at
    assert result.due_at is not None


def test_due_is_stored_as_utc() -> None:
    result = _parse("call the mairie tomorrow")

    assert result.due_at == datetime(2026, 9, 16, 7, 0, tzinfo=UTC)  # Paris is UTC+2 in September


def test_winter_date_uses_the_winter_offset() -> None:
    """The same 09:00 Paris is a different UTC hour once summer time ends."""
    result = _parse("renew insurance 2026-12-31")

    assert result.due_at == datetime(2026, 12, 31, 8, 0, tzinfo=UTC)  # Paris is UTC+1 in December


def test_us_is_refused_with_the_one_owner_rule() -> None:
    result = _parse("@us do the dishes")

    assert result.error == ONE_OWNER_ERROR
    assert not result.ok


def test_priority_marker_is_answered_with_a_warning() -> None:
    result = _parse("!urgent fix the boiler")

    assert result.warnings
    assert "!urgent" in result.warnings[0]
    assert "riorit" in result.warnings[0]


def test_message_without_a_title_is_refused() -> None:
    result = _parse("#move")

    assert result.error == NO_TITLE_ERROR


def test_empty_message_is_refused() -> None:
    result = _parse("   ")

    assert result.error == NO_TITLE_ERROR


def test_default_project_is_used_when_no_marker_is_present() -> None:
    result = _parse("book the movers", default_project="move")

    assert result.project == "move"


def test_explicit_project_beats_the_default() -> None:
    result = _parse("#paperwork book an appointment", default_project="move")

    assert result.project == "paperwork"


def test_unknown_short_leaves_the_sender_as_owner() -> None:
    result = _parse("@bob water the plants")

    assert result.owner == "robin"
    assert result.title == "@bob water the plants"


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        parse_task("buy milk", now=datetime(2026, 9, 15, 10, 30), sender_short="robin")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12", 12), ("#12", 12), ("  #12  ", 12), ("0", 0), ("", None), ("abc", None), ("#", None)],
)
def test_task_reference_accepts_both_forms(raw: str, expected: int | None) -> None:
    assert parse_task_ref(raw) == expected
