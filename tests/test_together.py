"""What two people sharing a bot need: attribution, twins, repeats, closed offices."""

from __future__ import annotations

from datetime import date, datetime

from bot import render
from bot.config import Config
from bot.db import Database
from bot.handlers.callbacks import _apply
from bot.parser import DEFAULT_TZ
from bot.services.holidays import easter_sunday, french_holiday
from bot.services.tasks import (
    Task,
    complete_task,
    copy_for,
    create_from_text,
    create_task,
    find_similar,
)
from bot.services.users import User
from tests.conftest import NOW, ROBIN_ID, SAM_ID


def _task(db: Database, title: str, *, owner: int = ROBIN_ID, **kwargs: object) -> Task:
    return create_task(
        db,
        title=title,
        owner_id=owner,
        created_by=ROBIN_ID,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


# --- months ----------------------------------------------------------------


def test_the_month_buttons_land_on_the_same_day_of_a_later_month(
    db: Database, robin: User, config: Config
) -> None:
    task = _task(
        db, "renew the titre de séjour", due_at=datetime(2026, 9, 15, 14, 0, tzinfo=DEFAULT_TZ)
    )

    one, toast = _apply("when_1m", db, task_id=task.id, now=NOW, config=config)
    assert one is not None and one.due_at is not None
    assert one.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 10, 15, 14, 0, tzinfo=DEFAULT_TZ)
    assert "Thu 15 Oct" in toast

    three, _ = _apply("when_3m", db, task_id=task.id, now=NOW, config=config)
    assert three is not None and three.due_at is not None
    assert three.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 12, 15, 14, 0, tzinfo=DEFAULT_TZ)


def test_three_months_from_the_end_of_a_long_month_clamps(
    db: Database, robin: User, config: Config
) -> None:
    """31 August plus three months is 30 November, not the 1st of December."""
    now = datetime(2026, 8, 31, 10, 0, tzinfo=DEFAULT_TZ)
    task = _task(db, "renew it", due_at=now)

    moved, _ = _apply("when_3m", db, task_id=task.id, now=now, config=config)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(DEFAULT_TZ).date() == date(2026, 11, 30)


def test_a_month_out_from_an_undated_task_uses_the_default_hour(
    db: Database, robin: User, config: Config
) -> None:
    task = _task(db, "someday")

    moved, _ = _apply("when_1m", db, task_id=task.id, now=NOW, config=config)

    assert moved is not None and moved.due_at is not None
    assert moved.due_at.astimezone(DEFAULT_TZ) == datetime(2026, 10, 15, 9, 0, tzinfo=DEFAULT_TZ)


# --- who asked whom --------------------------------------------------------


def test_a_card_says_who_asked_for_it(db: Database, robin: User, sam: User) -> None:
    outcome = create_from_text(db, "@sam call the landlord", sender=robin, now=NOW)
    assert outcome.task is not None

    text = render.task_card(outcome.task, sam, now=NOW, creator=robin)

    assert "👤 sam · asked by robin" in text


def test_a_card_stays_quiet_when_you_wrote_it_yourself(db: Database, robin: User) -> None:
    task = _task(db, "buy milk")

    text = render.task_card(task, robin, now=NOW, creator=robin)

    assert "asked by" not in text


# --- the same errand twice -------------------------------------------------


def test_the_second_person_writing_the_same_thing_is_warned(
    db: Database, robin: User, sam: User
) -> None:
    create_from_text(db, "call the landlord about the notice", sender=robin, now=NOW)

    outcome = create_from_text(db, "Call the landlord about the notice!", sender=sam, now=NOW)

    assert outcome.task is not None  # it is still created — this is a hint, not a veto
    assert outcome.duplicate is not None
    assert outcome.duplicate.title == "call the landlord about the notice"


def test_a_different_errand_is_not_flagged(db: Database, robin: User) -> None:
    create_from_text(db, "call the landlord about the notice", sender=robin, now=NOW)

    outcome = create_from_text(db, "book the movers for October", sender=robin, now=NOW)

    assert outcome.duplicate is None


def test_a_finished_task_does_not_haunt_the_next_one(db: Database, robin: User) -> None:
    first = create_from_text(db, "pay the timbre fiscal", sender=robin, now=NOW).task
    assert first is not None
    complete_task(db, first.id, now=NOW)

    outcome = create_from_text(db, "pay the timbre fiscal", sender=robin, now=NOW)

    assert outcome.duplicate is None


def test_very_short_titles_are_left_alone(db: Database, robin: User) -> None:
    create_from_text(db, "bank", sender=robin, now=NOW)

    outcome = create_from_text(db, "bank", sender=robin, now=NOW)

    assert outcome.duplicate is None


def test_find_similar_ignores_punctuation_and_case(db: Database, robin: User) -> None:
    _task(db, "Scan the attestation d'accueil")

    found = find_similar(db, "scan the attestation daccueil")

    assert found is not None


# --- both of them ----------------------------------------------------------


def test_both_makes_one_task_each_never_one_shared(db: Database, robin: User, sam: User) -> None:
    original = _task(
        db,
        "sign at the mairie",
        project="paperwork",
        due_at=datetime(2026, 9, 20, 9, 0, tzinfo=DEFAULT_TZ),
    )

    twin = copy_for(db, original, owner_id=SAM_ID, created_by=ROBIN_ID, now=NOW)

    assert twin.id != original.id
    assert twin.owner_id == SAM_ID
    assert original.owner_id == ROBIN_ID
    assert twin.title == original.title
    assert twin.project == original.project
    assert twin.due_at == original.due_at
    assert twin.created_by == ROBIN_ID


def test_closing_one_half_leaves_the_other_open(db: Database, robin: User, sam: User) -> None:
    original = _task(db, "sign at the mairie")
    twin = copy_for(db, original, owner_id=SAM_ID, created_by=ROBIN_ID, now=NOW)

    complete_task(db, original.id, now=NOW)

    from bot.services.tasks import get_task

    still_open = get_task(db, twin.id)
    assert still_open is not None and still_open.status == "todo"


# --- French public holidays ------------------------------------------------


def test_easter_is_where_the_almanac_says() -> None:
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_the_moveable_feasts_follow_easter() -> None:
    assert french_holiday(date(2026, 4, 6)) == "Lundi de Pâques"
    assert french_holiday(date(2026, 5, 14)) == "Ascension"
    assert french_holiday(date(2026, 5, 25)) == "Lundi de Pentecôte"


def test_the_fixed_holidays_are_all_there() -> None:
    assert french_holiday(date(2026, 1, 1)) == "Jour de l'an"
    assert french_holiday(date(2026, 5, 1)) == "Fête du Travail"
    assert french_holiday(date(2026, 7, 14)) == "Fête nationale"
    assert french_holiday(date(2026, 12, 25)) == "Noël"


def test_an_ordinary_tuesday_is_not_a_holiday() -> None:
    assert french_holiday(date(2026, 9, 15)) is None


def test_a_card_warns_when_the_offices_are_shut(db: Database, robin: User) -> None:
    task = _task(db, "mairie appointment", due_at=datetime(2026, 7, 14, 9, 0, tzinfo=DEFAULT_TZ))

    text = render.task_card(task, robin, now=NOW)

    assert "📛 Fête nationale — public holiday, offices will be shut" in text


def test_a_card_on_a_working_day_says_nothing_about_holidays(db: Database, robin: User) -> None:
    task = _task(db, "mairie appointment", due_at=datetime(2026, 7, 13, 9, 0, tzinfo=DEFAULT_TZ))

    assert "📛" not in render.task_card(task, robin, now=NOW)


def test_holidays_can_be_turned_off_entirely(db: Database, robin: User) -> None:
    """Anybody whose offices are not French sets HOLIDAYS=off."""
    task = _task(db, "appointment", due_at=datetime(2026, 7, 14, 9, 0, tzinfo=DEFAULT_TZ))

    assert "📛" in render.task_card(task, robin, now=NOW, holidays="FR")
    assert "📛" not in render.task_card(task, robin, now=NOW, holidays="off")
