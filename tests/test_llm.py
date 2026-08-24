"""The optional second-chance parser (section 8). No test here touches the network."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from bot import render
from bot.config import Config
from bot.db import Database
from bot.parser import DEFAULT_TZ
from bot.services import llm
from bot.services.tasks import apply_draft, create_from_text, list_subtasks
from bot.services.users import User
from tests.conftest import NOW

LONG = "we really need to sort out the deposit before the inspection at the end of next month"
SHORT = "call the landlord"


def _with_key(config: Config, key: str | None = "test-key") -> Config:
    return replace(config, gemini_api_key=key)


def _answer(**fields: Any) -> dict[str, Any]:
    """A response shaped exactly like Gemini's."""
    payload = {"title": "", "owner": "", "project": "", "due": "", "subtasks": [], **fields}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


def _transport(answer: dict[str, Any]) -> llm.Transport:
    async def send(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        assert "generativelanguage.googleapis.com" in url
        assert api_key == "test-key"
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        return answer

    return send


# --- when it is asked at all -----------------------------------------------


def test_it_is_never_asked_without_a_key(config: Config) -> None:
    assert not llm.should_ask(LONG, has_date=False, config=_with_key(config, None))


def test_it_is_never_asked_when_the_rules_already_found_a_date(config: Config) -> None:
    assert not llm.should_ask(LONG, has_date=True, config=_with_key(config))


def test_it_is_never_asked_about_a_short_message(config: Config) -> None:
    assert not llm.should_ask(SHORT, has_date=False, config=_with_key(config))


def test_it_is_asked_about_a_dateless_sentence(config: Config) -> None:
    assert llm.should_ask(LONG, has_date=False, config=_with_key(config))


# --- reading the answer ----------------------------------------------------


def test_a_date_and_subtasks_come_back(config: Config) -> None:
    answer = _answer(
        title="sort out the deposit",
        due="2026-09-30T14:30",
        project="move",
        subtasks=["email the landlord", "book the inspection"],
    )

    draft = asyncio.run(
        llm.read_task(LONG, config=_with_key(config), now=NOW, transport=_transport(answer))
    )

    assert draft is not None
    assert draft.due_at == datetime(2026, 9, 30, 14, 30, tzinfo=DEFAULT_TZ)
    assert draft.project == "move"
    assert draft.subtasks == ("email the landlord", "book the inspection")
    assert draft.is_useful


def test_a_bare_date_means_the_usual_morning(config: Config) -> None:
    draft = asyncio.run(
        llm.read_task(
            LONG, config=_with_key(config), now=NOW, transport=_transport(_answer(due="2026-09-30"))
        )
    )

    assert draft is not None and draft.due_at == datetime(2026, 9, 30, 9, 0, tzinfo=DEFAULT_TZ)


def test_an_answer_with_nothing_in_it_is_not_useful(config: Config) -> None:
    draft = asyncio.run(
        llm.read_task(
            LONG,
            config=_with_key(config),
            now=NOW,
            transport=_transport(_answer(title="something")),
        )
    )

    assert draft is not None and not draft.is_useful


def test_a_date_in_the_past_is_refused(config: Config) -> None:
    """A model that hands back last year is wrong, not early."""
    draft = asyncio.run(
        llm.read_task(
            LONG, config=_with_key(config), now=NOW, transport=_transport(_answer(due="2019-01-01"))
        )
    )

    assert draft is not None and draft.due_at is None


def test_a_broken_answer_never_raises(config: Config) -> None:
    async def broken(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}

    assert (
        asyncio.run(llm.read_task(LONG, config=_with_key(config), now=NOW, transport=broken))
        is None
    )


def test_a_timeout_never_raises(config: Config) -> None:
    async def times_out(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        raise TimeoutError("too slow")

    assert (
        asyncio.run(llm.read_task(LONG, config=_with_key(config), now=NOW, transport=times_out))
        is None
    )


def test_nothing_is_sent_without_a_key(config: Config) -> None:
    async def must_not_run(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        raise AssertionError("the model must not be called without a key")

    assert (
        asyncio.run(
            llm.read_task(LONG, config=_with_key(config, None), now=NOW, transport=must_not_run)
        )
        is None
    )


def test_the_prompt_carries_the_clock_and_the_people(config: Config) -> None:
    prompt = llm.build_prompt(LONG, now=NOW, tz=DEFAULT_TZ, shorts=("robin", "sam"))

    assert "2026-09-15 10:30" in prompt
    assert "Europe/Paris" in prompt
    assert "robin, sam" in prompt
    assert LONG in prompt


# --- what it is allowed to change ------------------------------------------


def test_it_fills_the_gaps_and_leaves_the_title_alone(db: Database, robin: User) -> None:
    outcome = create_from_text(db, LONG, sender=robin, now=NOW)
    assert outcome.task is not None and outcome.task.due_at is None

    improved = apply_draft(
        db,
        outcome.task,
        due_at=datetime(2026, 9, 30, 14, 30, tzinfo=DEFAULT_TZ),
        project="move",
        subtasks=["email the landlord", "book the inspection"],
        now=NOW,
    )

    assert improved.title == outcome.task.title  # never rewritten
    assert improved.due_at == datetime(2026, 9, 30, 14, 30, tzinfo=DEFAULT_TZ)
    assert improved.remind_at == improved.due_at
    assert improved.project == "move"
    assert [child.title for child in list_subtasks(db, improved.id)] == [
        "email the landlord",
        "book the inspection",
    ]


def test_it_never_overwrites_what_the_rules_already_decided(db: Database, robin: User) -> None:
    outcome = create_from_text(db, "#move pay the deposit tomorrow", sender=robin, now=NOW)
    assert outcome.task is not None
    original_due = outcome.task.due_at

    improved = apply_draft(
        db,
        outcome.task,
        due_at=NOW + timedelta(days=40),
        project="something-else",
        subtasks=[],
        now=NOW,
    )

    assert improved.due_at == original_due
    assert improved.project == "move"


def test_the_subtask_note_lists_the_steps(db: Database, robin: User) -> None:
    outcome = create_from_text(db, LONG, sender=robin, now=NOW)
    assert outcome.task is not None

    text = render.added_subtasks(outcome.task, ["email the landlord", "book the inspection"])

    assert "2 steps" in text
    assert "· email the landlord" in text


def test_the_outcome_remembers_the_message_it_came_from(db: Database, robin: User) -> None:
    outcome = create_from_text(db, LONG, sender=robin, now=NOW)

    assert outcome.source == LONG


def test_health_says_whether_the_second_chance_parser_is_on(db: Database) -> None:
    from bot.services.health import check

    off = render.health(check(db, now=NOW))
    on = render.health(check(db, now=NOW, llm_model="gemini-3.5-flash-lite"))

    assert "off — no GEMINI_API_KEY" in off
    assert "nothing leaves this machine" in off
    assert "on (gemini-3.5-flash-lite)" in on
    assert "over eight words has no date" in on


def test_a_failed_call_is_remembered_and_shown(db: Database, config: Config) -> None:
    """A 404 that only reaches the log is a feature nobody can debug."""
    from bot.handlers import _remember_llm_error
    from bot.services.health import check

    async def refuses(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        raise RuntimeError("HTTP 404: model not found")

    asyncio.run(
        llm.read_task(
            LONG,
            config=_with_key(config),
            now=NOW,
            transport=refuses,
            on_error=lambda reason: _remember_llm_error(db, reason),
        )
    )

    text = render.health(check(db, now=NOW, llm_model="gemini-3.5-flash-lite"))
    assert "failed last time" in text
    assert "HTTP 404: model not found" in text


def test_a_working_call_clears_the_old_failure(db: Database, config: Config) -> None:
    from bot.handlers import _remember_llm_error
    from bot.services.health import check

    _remember_llm_error(db, "RuntimeError: HTTP 404")

    asyncio.run(
        llm.read_task(
            LONG,
            config=_with_key(config),
            now=NOW,
            transport=_transport(_answer(due="2026-09-30")),
            on_error=lambda reason: _remember_llm_error(db, reason),
        )
    )

    assert "failed last time" not in render.health(check(db, now=NOW, llm_model="m"))


def test_an_enriched_card_keeps_its_heading(db: Database, robin: User, config: Config) -> None:
    """Re-rendering must not quietly turn "✍️ Added" into a bare card."""
    from bot.handlers import creation_text

    outcome = create_from_text(db, LONG, sender=robin, now=NOW)
    assert outcome.task is not None

    before = creation_text(db, outcome, now=NOW, config=config)
    improved = apply_draft(
        db, outcome.task, due_at=None, project="admin", subtasks=["a step"], now=NOW
    )
    after = creation_text(db, replace(outcome, task=improved), now=NOW, config=config)

    assert before.startswith("✍️ Added")
    assert after.startswith("✍️ Added")
    assert "admin" in after
    assert "admin" in after
