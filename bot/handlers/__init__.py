"""Telegram-facing glue. Handlers translate updates into service calls, nothing more."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message
from aiogram.types import User as TelegramUser

from bot import dashboard, render
from bot.config import Config
from bot.db import Database
from bot.services import llm, outbox
from bot.services import tasks as task_service
from bot.services.insights import build_insights
from bot.services.settings import LLM_LAST_ERROR, clear_setting, group_chat_id, set_setting
from bot.services.stats import build_board
from bot.services.tasks import CreateOutcome, Task
from bot.services.users import (
    User,
    ensure_user,
    get_user,
    known_shorts,
    list_users,
    partner_of,
)

logger = logging.getLogger(__name__)


def register_sender(message: Message, db: Database) -> User | None:
    """Make sure the sender exists in `users`, capturing their DM chat when we are in one.

    Returns None only when Telegram gave us no sender, which the whitelist
    middleware has already made impossible for anything that reaches a handler.
    """
    return register_person(message.from_user, message.chat, db)


def register_presser(callback: CallbackQuery, db: Database) -> User | None:
    """Register whoever pressed the button.

    Never `callback.message.from_user`: the message carrying the buttons was sent
    by the bot, so that field is the bot itself.
    """
    chat = callback.message.chat if isinstance(callback.message, Message) else None
    return register_person(callback.from_user, chat, db)


def register_person(sender: TelegramUser | None, chat: Chat | None, db: Database) -> User | None:
    """Add a human to `users`. Bots are never users of this bot — not even this one."""
    if sender is None:  # pragma: no cover - dropped by the whitelist middleware
        return None
    if sender.is_bot:
        logger.warning("refusing to register bot %s as a user", sender.id)
        return None
    return ensure_user(
        db,
        telegram_id=sender.id,
        username=sender.username,
        first_name=sender.first_name,
        last_name=sender.last_name,
        dm_chat_id=chat.id if chat is not None and chat.type == ChatType.PRIVATE else None,
    )


def owner_of(db: Database, task: Task) -> User:
    owner = get_user(db, task.owner_id)
    if owner is None:  # pragma: no cover - owner_id is a foreign key
        raise RuntimeError(f"task {task.id} has no owner row")
    return owner


def card_text(db: Database, task: Task, *, now: datetime, config: Config) -> str:
    return render.task_card(
        task,
        owner_of(db, task),
        now=now,
        tz=config.tz,
        creator=get_user(db, task.created_by),
        blockers=task_service.blockers_of(db, task.id),
        holidays=config.holidays,
        subtasks=task_service.subtask_progress(db, [task.id]).get(task.id),
    )


def announce_unblocked(db: Database, completed: Task, *, now: datetime, config: Config) -> int:
    """Say in the group that closing this freed something else (section 10).

    Queued like every other notification, so it waits for the end of the owner's
    quiet hours, and remembered in `notifications_sent` so a restart says nothing
    twice.
    """
    chat_id = group_chat_id(db)
    if chat_id is None:
        return 0
    said = 0
    day = outbox.local_day(now, config.tz)
    for task in task_service.newly_unblocked(db, completed.id):
        if not outbox.claim_saying(db, task_id=task.id, kind="unblocked", day=day):
            continue  # the same claim that stops a restart repeating it
        owner = owner_of(db, task)
        outbox.queue(
            db,
            chat_id=chat_id,
            text=render.unblocked(task, owner, now=now, tz=config.tz),
            send_after=outbox.release_at(now, owner, tz=config.tz),
        )
        said += 1
    return said


def card_markup(db: Database, task: Task) -> InlineKeyboardMarkup | None:
    """The buttons belonging to a task, with the partner named on the "Give to" one."""
    return render.task_keyboard(task, partner=partner_of(db, task.owner_id))


async def send_card(
    message: Message, db: Database, task: Task, *, now: datetime, config: Config, lead: str = ""
) -> None:
    text = card_text(db, task, now=now, config=config)
    dashboard.touch()
    await message.answer(lead + text, reply_markup=card_markup(db, task))


async def refresh_card(
    message: Message, db: Database, task: Task, *, now: datetime, config: Config
) -> None:
    """Update a card in place (section 13). Telegram rejects an edit that changes nothing."""
    dashboard.touch()
    try:
        await message.edit_text(
            card_text(db, task, now=now, config=config), reply_markup=card_markup(db, task)
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


def _remember_llm_error(db: Database, reason: str | None) -> None:
    """So /health can say why an optional feature is quietly doing nothing."""
    if reason is None:
        clear_setting(db, LLM_LAST_ERROR)
    else:
        set_setting(db, LLM_LAST_ERROR, reason)


def build_view(
    view: str,
    db: Database,
    *,
    user: User,
    now: datetime,
    config: Config,
    page: int = 0,
    query: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    """One view, rendered the same way whether a command or a menu button asked for it."""
    owners = {person.telegram_id: person for person in list_users(db)}

    if view == "find":
        found = task_service.search(db, query) if query else []
        text = render.found_list(found, owners, query=query, now=now, tz=config.tz)
        return text, render.list_keyboard(found, view="menu")
    if view == "today":
        tasks = task_service.list_due_today(db, now=now, tz=config.tz)
        blocked = task_service.blocked_map(db, tasks)
        text = render.today_list(tasks, owners, now=now, tz=config.tz, blocked=blocked)
    elif view == "week":
        tasks = task_service.list_week(db, now=now, tz=config.tz)
        blocked = task_service.blocked_map(db, tasks)
        text = render.week_list(tasks, owners, now=now, tz=config.tz, blocked=blocked)
    elif view == "month":
        tasks = task_service.list_month(db, now=now, tz=config.tz)
        blocked = task_service.blocked_map(db, tasks)
        text = render.month_list(tasks, owners, now=now, tz=config.tz, blocked=blocked)
    elif view == "overdue":
        tasks = task_service.list_overdue(db, now=now)
        blocked = task_service.blocked_map(db, tasks)
        text = render.overdue_list(tasks, owners, now=now, tz=config.tz, blocked=blocked)
    elif view == "mine":
        tasks = task_service.list_open_for(db, user.telegram_id)
        blocked = task_service.blocked_map(db, tasks)
        text = render.open_list(
            tasks,
            title=f"Open tasks for {user.short}",
            now=now,
            tz=config.tz,
            blocked=blocked,
        )
    elif view == "search":  # pragma: no cover - alias kept out of the menu
        return render.MENU_TEXT, render.menu_keyboard()
    elif view == "stats":
        report = build_insights(db, now=now, tz=config.tz)
        return render.stats(report, tz=config.tz), render.stats_keyboard()
    elif view == "board":
        board = build_board(db, now=now, tz=config.tz)
        return render.board(board, tz=config.tz), render.board_keyboard()
    elif view == "help":
        return render.help_text(), render.menu_keyboard()
    else:
        return render.MENU_TEXT, render.menu_keyboard()

    shown, page, pages = render.page_of(tasks, page)
    if pages > 1:
        text = _only_page(text, shown, tasks, now=now, tz=config.tz, owners=owners, view=view)
    return text, render.list_keyboard(shown, view=view, page=page, pages=pages)


def _only_page(
    full: str,
    shown: list[Task],
    tasks: list[Task],
    *,
    now: datetime,
    tz: object,
    owners: dict[int, User],
    view: str,
) -> str:
    """Re-render just this page, keeping the heading the full view produced."""
    del full, tz
    heading = {
        "today": "Today",
        "week": "The next seven days",
        "month": "The month ahead",
        "overdue": "Overdue",
        "mine": "Your open tasks",
    }.get(view, "Tasks")
    rows = render.open_list(shown, title=heading, now=now)
    return rows + f"\n\n<i>{len(shown)} of {len(tasks)} shown</i>"


def creation_text(db: Database, outcome: CreateOutcome, *, now: datetime, config: Config) -> str:
    """The full creation message: the lead, the card, and anything the parser complained about.

    Shared with the second-chance parser so an enriched card keeps its heading
    instead of quietly turning into a bare card.
    """
    if outcome.task is None:  # pragma: no cover - callers check first
        return render.with_warnings(outcome.error or render.BAD_TASK_REF, outcome.warnings)
    warnings = list(outcome.warnings)
    if outcome.duplicate is not None:
        warnings.append(render.duplicate_hint(outcome.duplicate))
    return render.with_warnings(
        "✍️ Added\n" + card_text(db, outcome.task, now=now, config=config), warnings
    )


async def answer_creation(
    message: Message, outcome: CreateOutcome, db: Database, *, now: datetime, config: Config
) -> None:
    """The reply to /add, /sub and a plain-text task: a card, plus any parser complaint."""
    if outcome.task is None:
        await message.answer(
            render.with_warnings(outcome.error or render.BAD_TASK_REF, outcome.warnings)
        )
        return
    text = creation_text(db, outcome, now=now, config=config)
    dashboard.touch()
    sent = await message.answer(text, reply_markup=card_markup(db, outcome.task))
    await second_chance(sent, db, outcome, now=now, config=config)


async def second_chance(
    card: Message, db: Database, outcome: CreateOutcome, *, now: datetime, config: Config
) -> None:
    """Give a dateless sentence to the model, once the task is already safe.

    The card is on screen before this runs, so a slow or broken model costs the
    person nothing: the rule-based task simply stays as it is.
    """
    task = outcome.task
    if task is None or not llm.should_ask(
        outcome.source, has_date=task.due_at is not None, config=config
    ):
        return

    draft = await llm.read_task(
        outcome.source,
        config=config,
        now=now,
        tz=config.tz,
        shorts=tuple(sorted(known_shorts(db))),
        on_error=lambda reason: _remember_llm_error(db, reason),
    )
    if draft is None or not draft.is_useful:
        return

    improved = task_service.apply_draft(
        db,
        task,
        due_at=draft.due_at,
        project=draft.project,
        subtasks=draft.subtasks,
        now=now,
    )
    dashboard.touch()
    try:
        await card.edit_text(
            creation_text(db, replace(outcome, task=improved), now=now, config=config),
            reply_markup=card_markup(db, improved),
        )
    except TelegramBadRequest as error:  # pragma: no cover - identical text
        if "message is not modified" not in str(error):
            raise
    if draft.subtasks:
        await card.answer(render.added_subtasks(improved, draft.subtasks))
