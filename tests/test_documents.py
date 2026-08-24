"""Phase 6: the document vault, the export, and the pinned dashboard."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

from bot import render
from bot.config import Config
from bot.dashboard import build_text
from bot.db import Database
from bot.services import docs as doc_service
from bot.services.export import export_csv, export_json
from bot.services.settings import bind_group
from bot.services.tasks import Task, add_dependency, complete_task, create_task
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


def _file(db: Database, name: str = "attestation.pdf", **kwargs: object) -> doc_service.Attachment:
    defaults: dict[str, object] = {
        "file_id": f"FILE-{name}",
        "file_unique_id": f"U-{name}",
        "file_name": name,
        "mime": "application/pdf",
        "kind": "document",
        "added_by": ROBIN_ID,
        "added_at": NOW,
    }
    return doc_service.store(db, **{**defaults, **kwargs})  # type: ignore[arg-type]


# --- keeping a file --------------------------------------------------------


def test_a_file_is_kept_before_anybody_says_where(db: Database, robin: User) -> None:
    """Take the scan first, ask second: the file must never be the thing that is lost."""
    stored = _file(db)

    assert stored.task_id is None
    assert stored.file_id == "FILE-attestation.pdf"
    assert doc_service.get_attachment(db, stored.id) == stored


def test_a_file_can_be_filed_against_a_task(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation")
    stored = _file(db)

    filed = doc_service.attach_to(db, stored.id, task.id)

    assert filed is not None and filed.task_id == task.id
    assert [item.id for item in doc_service.attachments_of(db, task.id)] == [stored.id]


def test_a_file_can_be_kept_without_a_task(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation")
    stored = _file(db)
    doc_service.attach_to(db, stored.id, task.id)

    loose = doc_service.attach_to(db, stored.id, None)

    assert loose is not None and loose.task_id is None


def test_the_intake_offers_the_five_newest_open_tasks(db: Database, robin: User) -> None:
    for index in range(7):
        _task(db, f"task {index}")
    done = _task(db, "already finished")
    complete_task(db, done.id, now=NOW)

    offered = doc_service.recently_touched(db)

    assert len(offered) == doc_service.RECENT_OFFERED
    assert "already finished" not in [task.title for task in offered]
    assert offered[0].title == "task 6"  # newest first


# --- finding it again ------------------------------------------------------


def test_docs_searches_every_field_that_matters(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation d'accueil", project="paperwork")
    by_name = _file(db, "attestation.pdf")
    by_caption = _file(db, "scan001.jpg", caption="the signed attestation", kind="photo")
    by_task = _file(db, "unnamed.pdf")
    doc_service.attach_to(db, by_task.id, task.id)

    assert {found.id for found in doc_service.search(db, "attestation")} == {
        by_name.id,
        by_caption.id,
        by_task.id,
    }
    assert [found.id for found in doc_service.search(db, "PAPERWORK")] == [by_task.id]
    assert doc_service.search(db, "nothing here") == []
    assert doc_service.search(db, "   ") == []


def test_docs_sends_back_at_most_ten(db: Database, robin: User) -> None:
    for index in range(15):
        _file(db, f"mairie-{index}.pdf")

    assert len(doc_service.search(db, "mairie")) == doc_service.SEARCH_LIMIT


def test_searching_for_a_task_to_file_against(db: Database, robin: User) -> None:
    wanted = _task(db, "book the mairie appointment", project="paperwork")
    _task(db, "buy milk")

    assert [task.id for task in doc_service.search_tasks(db, "mairie")] == [wanted.id]
    assert [task.id for task in doc_service.search_tasks(db, "paper")] == [wanted.id]


def test_a_returned_file_is_captioned_with_its_task(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation", project="paperwork")
    stored = _file(db)
    filed = doc_service.attach_to(db, stored.id, task.id)
    assert filed is not None

    assert render.doc_caption(filed, task) == f"#{task.id} collect the attestation · #paperwork"
    assert render.doc_caption(filed, None) == "📥 no task"


def test_the_intake_keyboard_offers_search_and_keep(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation")
    stored = _file(db)

    markup = render.intake_keyboard(stored, [task])
    labels = [button.text for row in markup.inline_keyboard for button in row]
    payloads = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert f"#{task.id} collect the attestation" in labels
    assert "🔍 Search" in labels
    assert "📥 Keep without a task" in labels
    assert f"d:{stored.id}:{task.id}" in payloads
    assert f"d:{stored.id}:0" in payloads  # keep it loose


# --- the export ------------------------------------------------------------


def test_the_csv_holds_every_task_and_no_priority(db: Database, robin: User, sam: User) -> None:
    first = _task(db, "book the movers", project="move", due_at=NOW)
    second = _task(db, "call the mairie", owner=SAM_ID)
    add_dependency(db, second.id, first.id)
    complete_task(db, first.id, now=NOW)

    rows = list(csv.DictReader(io.StringIO(export_csv(db))))

    assert [row["title"] for row in rows] == ["book the movers", "call the mairie"]
    assert rows[0]["owner"] == "robin"
    assert rows[1]["owner"] == "sam"
    assert rows[0]["status"] == "done"
    assert rows[1]["blocked_by"] == f"#{first.id}"
    assert not any("priorit" in column.lower() for column in rows[0])


def test_the_json_dump_carries_the_attachments(db: Database, robin: User) -> None:
    task = _task(db, "collect the attestation")
    stored = _file(db)
    doc_service.attach_to(db, stored.id, task.id)

    dump = json.loads(export_json(db))

    assert [item["title"] for item in dump["tasks"]] == ["collect the attestation"]
    assert dump["attachments"][0]["file_id"] == stored.file_id
    assert dump["attachments"][0]["task_id"] == task.id


def test_an_empty_database_still_exports(db: Database) -> None:
    assert export_csv(db).startswith("id,parent_id,title")
    assert json.loads(export_json(db)) == {"tasks": [], "attachments": []}


# --- the pinned dashboard --------------------------------------------------


def test_the_dashboard_shows_today_the_counts_and_what_is_next(
    db: Database, robin: User, sam: User, config: Config
) -> None:
    bind_group(db, -100_555)
    _task(db, "pack the kitchen", due_at=NOW + timedelta(hours=3))
    _task(db, "call the landlord", owner=SAM_ID, due_at=NOW + timedelta(hours=5))
    _task(db, "pay the deposit", due_at=NOW - timedelta(days=2))
    _task(db, "the week after", due_at=NOW + timedelta(days=4))

    text = build_text(db, now=NOW, config=config)

    assert "📌 <b>Today" in text
    assert "pack the kitchen" in text
    assert "<b>robin</b>" in text and "<b>sam</b>" in text
    assert "⚠️ 1 overdue" in text
    assert "<b>Next up</b>" in text
    assert len(text) <= render.DASHBOARD_LIMIT


def test_the_dashboard_says_when_today_is_empty(db: Database, robin: User, config: Config) -> None:
    text = build_text(db, now=NOW, config=config)

    assert render.NOTHING_TODAY in text
    assert "Nothing late, nothing waiting." in text
    assert "0 overdue" not in text


def test_the_dashboard_stays_under_the_limit(db: Database, robin: User, config: Config) -> None:
    for index in range(200):
        _task(db, f"a fairly long task title number {index} about the move", due_at=NOW)

    text = build_text(db, now=NOW, config=config)

    assert len(text) <= render.DASHBOARD_LIMIT
    assert text.endswith("…")


def test_a_blocked_task_is_greyed_on_the_dashboard(
    db: Database, robin: User, config: Config
) -> None:
    blocker = _task(db, "collect the payslips")
    blocked = _task(db, "book the mairie", due_at=NOW)
    add_dependency(db, blocked.id, blocker.id)

    text = build_text(db, now=NOW, config=config)

    assert render.GLYPH_BLOCKED in text
    assert f"blocked by #{blocker.id}" in text
