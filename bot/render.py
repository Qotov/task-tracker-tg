"""Every string and every keyboard the bot sends, built here.

Handlers never format anything themselves. Times arrive as UTC and are rendered in
the display timezone; task ids are always shown as `#12`. Callback data follows
section 13: a short prefix, an action, and the task id — `t:done:12`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.parser import DEFAULT_TZ
from bot.services.digest import Digest
from bot.services.docs import Attachment
from bot.services.health import Health
from bot.services.holidays import holiday_name
from bot.services.recurrence import parse_recurrence
from bot.services.stats import Board
from bot.services.tasks import Task
from bot.services.users import User

NOTHING_TODAY = "Nothing due today. 🎉"
NOTHING_OPEN = "No open tasks here. 🎉"
NOTHING_OVERDUE = "Nothing overdue. 🎉"
NOTHING_THIS_WEEK = "Nothing due in the next seven days."
NOTHING_THIS_MONTH = "Nothing due in the next thirty days."
UNKNOWN_TASK = "There is no task <b>#{task_id}</b>."
BAD_TASK_REF = "Give me a task id, like <code>/done 12</code>."
NO_PARTNER = "Nobody else has started the bot yet."

SUBTASK_PROMPT = "What is the subtask of #{task_id}? Send the title in your next message."
SUBTASK_EXPIRED = (
    "That subtask prompt was more than five minutes old, so I added this as a normal task."
)
NOTE_PROMPT = "What should I note on #{task_id}? Send it in your next message."
NOTE_EXPIRED = "That note prompt was more than five minutes old, so I let it go."

NEW_TASK_PROMPT = (
    "➕ <b>What needs doing?</b>\n"
    "Send it in one message. Anything you put in it is understood:\n"
    "· <b>@name</b> — whose it is (leave it out and it is yours)\n"
    "· <b>#project</b> — which pile it belongs to\n"
    "· <b>tomorrow</b>, <b>20/09</b>, <b>+3d</b>, <b>fri 18:00</b> — when it is due\n\n"
    "<i>@name call the landlord tomorrow #move</i>"
)
CANCELLED = "Nothing added."

#: Labels on the keyboard under the text field, matched back in the handlers.
HOME_TODAY = "📅 Today"
HOME_WEEK = "🗓 Week"
HOME_MONTH = "📆 Month"
HOME_OVERDUE = "⚠️ Overdue"
HOME_MINE = "📋 Mine"
HOME_BOARD = "📊 Board"
HOME_NEW = "➕ New task"
HOME_MENU = "☰ Menu"

#: How many tasks in a list get their own "open this one" button.
OPENABLE_IN_LIST = 8


class TaskAction(CallbackData, prefix="t"):
    """Button payload on a task card: renders as `t:done:12`."""

    action: str
    task_id: int


class MenuAction(CallbackData, prefix="m"):
    """Button payload for navigation: renders as `m:today`."""

    view: str


def start_text(user: User) -> str:
    return (
        f"Hello {escape(user.display_name)}, you are registered "
        f"as <b>@{escape(user.short)}</b>.\n\n"
        "Write anything and it becomes a task:\n"
        "<code>call the landlord tomorrow #move</code>\n"
        "<b>@name</b> sets the owner · <b>#project</b> the project · a date like "
        "<code>tomorrow</code>, <code>20/09</code>, <code>+3d</code> or <code>fri 18:00</code> "
        "sets when.\n\n"
        "Everything else is buttons — the keyboard below stays put, and every task "
        "card carries its own."
    )


MENU_TEXT = "☰ <b>Menu</b>\nPick a view. Anything you type becomes a task."


def help_text() -> str:
    """The commands that exist today. Later phases add to this list."""
    return (
        "<b>What I understand</b>\n\n"
        "Any plain message becomes a task. In the text:\n"
        "· <b>@name</b> or <b>@me</b> — who owns it (exactly one person)\n"
        "· <b>#project</b> — the project\n"
        "· a date — <code>today</code>, <code>tomorrow</code>, <code>mon</code>…<code>sun</code>, "
        "<code>20/09</code>, <code>20.09</code>, <code>24 Sep</code>, <code>2026-09-20</code>, "
        "<code>+3d</code>, <code>+2w</code>, <code>+1m</code>, "
        "any of them with a time like <code>14:30</code>\n"
        "A date without a time means 09:00. There are no priorities: "
        "the due date is the urgency.\n\n"
        "Every task card has buttons — <b>Done</b>, <b>+1 day</b>, <b>Waiting</b>, "
        "<b>Subtask</b>, <b>Note</b>, <b>Reschedule</b> (up to +3 months), <b>Drop</b>, "
        "and <b>Reopen</b> once it is closed — so you rarely need a command at all.\n\n"
        "<b>For the two of you</b>\n"
        "· <b>👤 →</b> hands a task over · <b>👥 Both</b> makes a copy for the other one, "
        "since a task has exactly one owner\n"
        "· a card says <i>asked by</i> when the other person wrote it for you\n"
        "· writing something the other one already added gets you a quiet nudge\n"
        "· a due date on a <i>jour férié</i> is flagged — the mairie will be shut\n\n"
        "<b>What I do on my own</b>\n"
        "· remind you when something is due, and once a day while it is late\n"
        "· a morning digest at your hour, and a nudge on anything you are waiting for\n"
        "· never between your quiet hours — it waits for the morning instead\n"
        "· say in the group when finishing one task frees another\n"
        "· send a document back when you ask for it\n\n"
        "<b>Commands</b>\n"
        "/menu — the button menu\n"
        "/new — add a task, guided\n"
        "/board — the tracker board\n"
        "/add &lt;text&gt; — add a task\n"
        "/sub &lt;id&gt; &lt;text&gt; — add a subtask under it\n"
        "/done &lt;id&gt; — close a task\n"
        "/drop &lt;id&gt; — abandon a task\n"
        "/due &lt;id&gt; &lt;date&gt; — change the due date\n"
        "/own &lt;id&gt; @who — hand it over\n"
        "/note &lt;id&gt; &lt;text&gt; — append a dated note\n"
        "/wait &lt;id&gt; [date] — parked on somebody else\n"
        "/block &lt;id&gt; after &lt;id&gt; — make one wait for the other\n"
        "/repeat &lt;id&gt; weekly:mon — or daily, monthly:15, yearly:09-20, off\n"
        "/today — due today or earlier, both of us\n"
        "/week — the next seven days\n"
        "/month — the next thirty, grouped by week\n"
        "/overdue — everything past due\n"
        "/mine — your open tasks\n"
        "/docs &lt;word&gt; — find a scan\n"
        "/export — every task as CSV and JSON\n"
        "/settings — digest hour, quiet hours, escalation\n"
        "/dash — rebuild the pinned dashboard\n"
        "/health — is everything working?\n"
        "/help — this message"
    )


# --- keyboards -------------------------------------------------------------


def task_keyboard(task: Task, *, partner: User | None = None) -> InlineKeyboardMarkup | None:
    """Everything you can do to one task, without typing (section 13, extended).

    A closed card keeps one button — reopening it — because closing the wrong task
    with a thumb is the easiest mistake to make here.
    """
    builder = InlineKeyboardBuilder()
    if task.status == "todo":
        builder.row(
            _button("✅ Done", "done", task.id),
            _button("📅 +1 day", "day1", task.id),
            _button("⏳ Waiting", "wait", task.id),
        )
        second = [_button("➕ Subtask", "sub", task.id)]
        if partner is not None:
            second.insert(0, _button(f"👤 → {partner.short}", "give", task.id))
            second.append(_button("👥 Both", "both", task.id))
        builder.row(*second)
        builder.row(
            _button("📝 Note", "note", task.id),
            _button("🕘 Reschedule", "when", task.id),
            _button("🗑 Drop", "drop", task.id),
        )
        return builder.as_markup()
    if task.status == "waiting":
        builder.row(
            _button("✅ Done", "done", task.id),
            _button("📅 +7 days", "day7", task.id),
            _button("↩️ To do", "todo", task.id),
        )
        builder.row(
            _button("📝 Note", "note", task.id),
            _button("🗑 Drop", "drop", task.id),
        )
        return builder.as_markup()
    builder.row(_button("↩️ Reopen", "reopen", task.id))
    return builder.as_markup()


def reschedule_keyboard(task: Task) -> InlineKeyboardMarkup:
    """The second level of the card: pick a new date without typing one."""
    builder = InlineKeyboardBuilder()
    builder.row(
        _button("Today", "when_today", task.id),
        _button("Tomorrow", "when_tomorrow", task.id),
        _button("+3 days", "when_3d", task.id),
    )
    builder.row(
        _button("Next week", "when_1w", task.id),
        _button("+1 month", "when_1m", task.id),
        _button("+3 months", "when_3m", task.id),
    )
    builder.row(
        _button("✖️ No date", "when_none", task.id),
        _button("← Back", "when_back", task.id),
    )
    return builder.as_markup()


def menu_keyboard() -> InlineKeyboardMarkup:
    """The inline menu: adding a task first, then every list, one press away."""
    builder = InlineKeyboardBuilder()
    builder.row(_view_button("➕ New task", "new"))
    builder.row(
        _view_button("📅 Today", "today"),
        _view_button("🗓 Week", "week"),
        _view_button("📆 Month", "month"),
    )
    builder.row(_view_button("⚠️ Overdue", "overdue"), _view_button("📋 Mine", "mine"))
    builder.row(_view_button("📊 Board", "board"), _view_button("❓ Help", "help"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Shown under a prompt, so changing your mind does not need a stray message."""
    builder = InlineKeyboardBuilder()
    builder.row(_view_button("✖️ Cancel", "cancel"))
    return builder.as_markup()


def list_keyboard(tasks: Iterable[Task], *, view: str) -> InlineKeyboardMarkup:
    """A list plus one button per task, so any of them can be opened with a thumb."""
    builder = InlineKeyboardBuilder()
    shown = list(tasks)[:OPENABLE_IN_LIST]
    for index in range(0, len(shown), 4):
        builder.row(*(_button(f"#{task.id}", "open", task.id) for task in shown[index : index + 4]))
    builder.row(_view_button("🔄 Refresh", view), _view_button("☰ Menu", "menu"))
    return builder.as_markup()


def board_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_view_button("🔄 Refresh", "board"), _view_button("📅 Today", "today"))
    builder.row(_view_button("⚠️ Overdue", "overdue"), _view_button("☰ Menu", "menu"))
    return builder.as_markup()


def home_keyboard() -> ReplyKeyboardMarkup:
    """The keyboard under the text field, in private chats: always one tap away."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=HOME_TODAY),
                KeyboardButton(text=HOME_WEEK),
                KeyboardButton(text=HOME_MONTH),
            ],
            [
                KeyboardButton(text=HOME_OVERDUE),
                KeyboardButton(text=HOME_MINE),
                KeyboardButton(text=HOME_BOARD),
            ],
            [KeyboardButton(text=HOME_NEW), KeyboardButton(text=HOME_MENU)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="…or just type a task",
    )


def _button(text: str, action: str, task_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=TaskAction(action=action, task_id=task_id).pack()
    )


def _view_button(text: str, view: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuAction(view=view).pack())


# --- cards -----------------------------------------------------------------


def task_card(
    task: Task,
    owner: User,
    *,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    creator: User | None = None,
    blockers: Iterable[Task] = (),
    holidays: str = "FR",
) -> str:
    """The block shown after a task is created or changed.

    `creator` is passed only when somebody else wrote the task, because "who asked
    me to do this" is the question two people actually have.
    """
    lines = [f"{_status_mark(task)}#{task.id} <b>{escape(task.title)}</b>"]

    details = [f"👤 {escape(owner.short)}"]
    if creator is not None and creator.telegram_id != owner.telegram_id:
        details[0] += f" · asked by {escape(creator.short)}"
    if task.due_at is not None:
        details.append(f"📅 {_when(task.due_at, now=now, tz=tz)}")
    if task.project is not None:
        details.append(f"🏷 #{escape(task.project)}")
    lines.append(" · ".join(details))

    waiting_for = list(blockers)
    if waiting_for:
        listed = ", ".join(f"#{blocker.id} {escape(blocker.title)}" for blocker in waiting_for)
        lines.append(f"🔒 blocked by {listed}")
    closed_day = _closed_day(task.due_at, tz=tz, region=holidays)
    if closed_day is not None:
        lines.append(f"📛 {escape(closed_day)} — public holiday, offices will be shut")
    if task.status == "waiting" and task.follow_up_at is not None:
        lines.append(f"⏳ waiting — chase it up {_when(task.follow_up_at, now=now, tz=tz)}")
    if task.recurrence is not None:
        rule = parse_recurrence(task.recurrence)
        if rule is not None:
            lines.append(f"🔁 repeats {rule.describe()}")
    if task.parent_id is not None:
        lines.append(f"↳ subtask of #{task.parent_id}")
    if task.notes:
        lines.append("📝 " + escape(task.notes).replace("\n", "\n   "))
    return "\n".join(lines)


def added_subtasks(task: Task, titles: Iterable[str]) -> str:
    """Said when the second-chance parser broke a sentence into steps."""
    steps = list(titles)
    listed = "\n".join(f"· {escape(title)}" for title in steps)
    return f"🪄 I read #{task.id} as {len(steps)} steps and added them:\n{listed}"


def duplicate_hint(existing: Task) -> str:
    """Said when a new task reads like one that is already on a list."""
    return (
        f"👀 <b>#{existing.id} {escape(existing.title)}</b> is already open and looks like "
        "the same thing — close one if it is."
    )


def _closed_day(due_at: datetime | None, *, tz: ZoneInfo, region: str) -> str | None:
    if due_at is None or region.upper() == "OFF":
        return None
    return holiday_name(due_at.astimezone(tz).date(), region=region)


def _status_mark(task: Task) -> str:
    return {"done": "✅ ", "dropped": "🗑 ", "waiting": "⏳ "}.get(task.status, "")


def created(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    return "✍️ Added\n" + task_card(task, owner, now=now, tz=tz)


def completed(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    return "✅ Done\n" + task_card(task, owner, now=now, tz=tz)


def repeated(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    return "🔁 Next one\n" + task_card(task, owner, now=now, tz=tz)


def already_done(task: Task) -> str:
    return f"#{task.id} <b>{escape(task.title)}</b> was already done."


def with_warnings(body: str, warnings: Iterable[str]) -> str:
    """Append parser complaints (a stray `!urgent`, say) under the message."""
    extra = list(warnings)
    if not extra:
        return body
    return body + "\n\n" + "\n".join(f"ℹ️ {line}" for line in extra)


# --- lists -----------------------------------------------------------------


def today_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """Tasks due today or earlier, grouped by owner."""
    grouped: dict[int, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(task.owner_id, []).append(task)

    header = f"<b>Today — {now.astimezone(tz):%a %d %b}</b>"
    if not grouped:
        return f"{header}\n\n{NOTHING_TODAY}"

    blocks: list[str] = []
    for owner_id, owned in sorted(grouped.items(), key=lambda item: _short_of(owners, item[0])):
        name = _short_of(owners, owner_id)
        rows = _rows(owned, now=now, tz=tz, blocked=blocked)
        blocks.append(f"<b>{escape(name)}</b>\n{rows}")
    return f"{header}\n\n" + "\n\n".join(blocks)


def week_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """The next seven days, grouped by day."""
    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        if task.due_at is None:  # pragma: no cover - the query only returns dated tasks
            continue
        grouped.setdefault(f"{task.due_at.astimezone(tz):%Y-%m-%d}", []).append(task)

    header = "<b>The next seven days</b>"
    if not grouped:
        return f"{header}\n\n{NOTHING_THIS_WEEK}"

    blocks: list[str] = []
    for day, due in sorted(grouped.items()):
        when = datetime.strptime(day, "%Y-%m-%d")
        rows = _rows(due, now=now, tz=tz, owners=owners, with_time=True, blocked=blocked)
        blocks.append(f"<b>{when:%a %d %b}</b>\n{rows}")
    return f"{header}\n\n" + "\n\n".join(blocks)


def month_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """The next thirty days, grouped by week — a month of single days would not read."""
    grouped: dict[date, list[Task]] = {}
    for task in tasks:
        if task.due_at is None:  # pragma: no cover - the query only returns dated tasks
            continue
        local = task.due_at.astimezone(tz).date()
        grouped.setdefault(local - timedelta(days=local.weekday()), []).append(task)

    header = "<b>The month ahead</b>"
    if not grouped:
        return f"{header}\n\n{NOTHING_THIS_MONTH}"

    this_week = _monday_of(now.astimezone(tz).date())
    blocks: list[str] = []
    for monday, due in sorted(grouped.items()):
        rows = _rows(due, now=now, tz=tz, owners=owners, blocked=blocked)
        blocks.append(f"<b>{_week_label(monday, this_week)}</b>\n{rows}")
    return f"{header}\n\n" + "\n\n".join(blocks)


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_label(monday: date, this_week: date) -> str:
    weeks = (monday - this_week).days // 7
    if weeks == 0:
        return "This week"
    if weeks == 1:
        return "Next week"
    return f"Week of {monday:%a %d %b}"


def overdue_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """Everything past due, oldest first."""
    listed = list(tasks)
    header = "<b>Overdue</b>"
    if not listed:
        return f"{header}\n\n{NOTHING_OVERDUE}"
    return f"{header}\n" + _rows(listed, now=now, tz=tz, owners=owners, blocked=blocked)


def open_list(
    tasks: Iterable[Task],
    *,
    title: str,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """A flat list, used by /mine."""
    listed = list(tasks)
    if not listed:
        return f"<b>{escape(title)}</b>\n\n{NOTHING_OPEN}"
    return f"<b>{escape(title)}</b>\n" + _rows(listed, now=now, tz=tz, blocked=blocked)


def _rows(
    tasks: Iterable[Task],
    *,
    now: datetime,
    tz: ZoneInfo,
    owners: Mapping[int, User] | None = None,
    with_time: bool = False,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """A block of list lines, with anything blocked pushed to the bottom (section 10)."""
    ordered = sorted(tasks, key=lambda task: task.id in (blocked or {}))
    return "\n".join(
        _row(task, now=now, tz=tz, owners=owners, with_time=with_time, blocked=blocked)
        for task in ordered
    )


def _row(
    task: Task,
    *,
    now: datetime,
    tz: ZoneInfo,
    owners: Mapping[int, User] | None = None,
    with_time: bool = False,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    blockers = (blocked or {}).get(task.id)
    if blockers:
        # Telegram has no grey, so a blocked line is locked and set in italics.
        waiting_for = ", ".join(f"#{blocker}" for blocker in blockers)
        return f"<i>🔒 #{task.id} {escape(task.title)} — blocked by {waiting_for}</i>"

    line = f"• #{task.id} {escape(task.title)}"
    bits: list[str] = []
    if task.due_at is not None:
        bits.append(
            f"{task.due_at.astimezone(tz):%H:%M}"
            if with_time
            else _when(task.due_at, now=now, tz=tz)
        )
    if owners is not None:
        bits.append(escape(_short_of(owners, task.owner_id)))
    if task.project is not None:
        bits.append(f"#{escape(task.project)}")
    if task.status == "waiting":
        bits.append("waiting")
    return line + (" — " + " · ".join(bits) if bits else "")


def unblocked(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """Posted in the group when the last thing in the way is finished (section 10)."""
    when = "" if task.due_at is None else f"\n📅 {_when(task.due_at, now=now, tz=tz)}"
    return (
        f"🔓 <b>{escape(task.title)}</b> is free to start.\n"
        f"#{task.id} · {escape(owner.short)}{when}"
    )


# --- what the bot says first -----------------------------------------------


def reminder(task: Task, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """A reminder says "this is due now" — never the ⚠️ that means "you are late".

    The tick runs once a minute, so a reminder is usually a few seconds past the
    due moment; scolding somebody for that would be absurd.
    """
    del now
    when = "" if task.due_at is None else f" — {task.due_at.astimezone(tz):%a %d %b %H:%M}"
    return f"🔔 <b>Reminder</b>\n{escape(task.title)}{when} · #{task.id}"


def overdue_ping(task: Task, *, days_late: int, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    del now
    late = "a day late" if days_late == 1 else f"{days_late} days late"
    when = "" if task.due_at is None else f", due {task.due_at.astimezone(tz):%a %d %b %H:%M}"
    return f"⚠️ <b>Still open</b>\n{escape(task.title)} — {late}{when} · #{task.id}"


def follow_up(task: Task, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    del now, tz
    return f"⏳ <b>Still waiting</b>\n{escape(task.title)} · #{task.id} — no answer yet?"


def follow_up_keyboard(task: Task) -> InlineKeyboardMarkup:
    """The two things you ever want to do about a stalled task."""
    builder = InlineKeyboardBuilder()
    builder.row(
        _button("✅ Done", "done", task.id),
        _button("📅 +7 days", "day7", task.id),
    )
    return builder.as_markup()


def escalation(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    when = "" if task.due_at is None else f" (due {_when(task.due_at, now=now, tz=tz)})"
    return (
        f"📣 <b>{escape(task.title)}</b>{when} has been sitting for three days.\n"
        f"#{task.id} · {escape(owner.short)}"
    )


def digest(data: Digest, *, now: datetime, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """The morning message: four sections, none of them shown when empty."""
    blocks = [f"☀️ <b>Good morning, {escape(data.user.short)} — {now.astimezone(tz):%a %d %b}</b>"]
    for title, tasks in (
        ("Due today", data.due_today),
        ("⚠️ Overdue", data.overdue),
        ("🔓 Came free", data.unblocked),
        ("⏳ Waiting on somebody", data.follow_ups),
    ):
        if tasks:
            rows = _rows(tasks, now=now, tz=tz)
            blocks.append(f"<b>{title}</b>\n{rows}")
    return "\n\n".join(blocks)


# --- settings --------------------------------------------------------------


class SettingAction(CallbackData, prefix="s"):
    """`s:digest:1` — which field, and which way to nudge it."""

    field: str
    step: int


def settings_text(user: User) -> str:
    return (
        "⚙️ <b>Your settings</b>\n\n"
        f"☀️ Digest at <b>{user.digest_hour:02d}:00</b>\n"
        f"🤫 Quiet from <b>{escape(user.quiet_start)}</b> to <b>{escape(user.quiet_end)}</b>\n"
        f"📣 Group escalation <b>{'on' if user.escalation else 'off'}</b>\n\n"
        "<i>Nothing is ever sent inside your quiet hours — it waits for the end of them.</i>"
    )


def settings_keyboard(user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _setting_button("−", "digest", -1),
        _setting_button(f"☀️ {user.digest_hour:02d}:00", "digest", 0),
        _setting_button("+", "digest", 1),
    )
    builder.row(
        _setting_button("−", "quiet_start", -1),
        _setting_button(f"🤫 from {user.quiet_start}", "quiet_start", 0),
        _setting_button("+", "quiet_start", 1),
    )
    builder.row(
        _setting_button("−", "quiet_end", -1),
        _setting_button(f"🔔 until {user.quiet_end}", "quiet_end", 0),
        _setting_button("+", "quiet_end", 1),
    )
    builder.row(
        _setting_button(
            f"📣 Escalation: {'on' if user.escalation else 'off'} — tap to turn "
            f"{'off' if user.escalation else 'on'}",
            "escalation",
            1,
        )
    )
    return builder.as_markup()


def _setting_button(text: str, field: str, step: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=SettingAction(field=field, step=step).pack()
    )


def health(report: Health, *, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """The answer to "is this thing working?", readable from a phone."""
    lines = [
        "🩺 <b>Health</b>",
        f"🕐 It is <b>{report.at.astimezone(tz):%H:%M}</b> in {escape(str(tz))} — "
        "the clock I work in. Set <code>TZ</code> in .env if that is not yours.",
    ]

    age = report.tick_age_seconds
    if report.ticking and age is not None:
        lines.append(f"✅ Scheduler alive — last tick {int(age)}s ago")
    elif age is None:
        lines.append("❌ Scheduler has never ticked. Is the bot actually running?")
    else:
        lines.append(f"❌ Scheduler stalled — last tick {int(age)}s ago")

    if report.queued:
        when = (
            ""
            if report.next_send is None
            else f", next at {report.next_send.astimezone(tz):%a %H:%M}"
        )
        lines.append(f"📬 {report.queued} message(s) held{when}")
    else:
        lines.append("📬 Nothing waiting to go out")

    lines.append(f"👥 {len(report.users)} registered · 📦 {report.open_tasks} open task(s)")
    if report.unreachable:
        names = ", ".join(escape(user.short) for user in report.unreachable)
        lines.append(f"⚠️ No private chat with {names} — they must send /start to me directly")
    lines.append(
        ("✅ Group linked" if report.group_bound else "⚠️ No group yet")
        + " · "
        + ("📌 dashboard pinned" if report.dashboard_pinned else "no dashboard pinned")
    )
    return "\n".join(lines)


# --- documents -------------------------------------------------------------

DOC_SEARCH_PROMPT = "Which task? Send a word from its title or project."
NO_DOCS_FOUND = (
    "Nothing matches <b>{query}</b>. I search titles, projects, file names and captions."
)
DOCS_USAGE = "Use <code>/docs mairie</code> — I search titles, projects, file names and captions."


#: Task id on the Search button, which asks for a word instead of filing anything.
DOC_SEARCH = -1


class DocAction(CallbackData, prefix="d"):
    """`d:3:12` — file attachment 3 against task 12. Task 0 means keep it loose."""

    attachment_id: int
    task_id: int


def intake_text(attachment: Attachment) -> str:
    name = attachment.file_name or ("a photo" if attachment.kind == "photo" else "that file")
    return f"📎 Kept <b>{escape(name)}</b>. Which task does it belong to?"


def intake_keyboard(attachment: Attachment, recent: Iterable[Task]) -> InlineKeyboardMarkup:
    """The five most recent open tasks, plus Search and Keep without a task."""
    builder = InlineKeyboardBuilder()
    for task in recent:
        builder.row(
            InlineKeyboardButton(
                text=f"#{task.id} {task.title[:40]}",
                callback_data=DocAction(attachment_id=attachment.id, task_id=task.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Search",
            callback_data=DocAction(attachment_id=attachment.id, task_id=DOC_SEARCH).pack(),
        ),
        InlineKeyboardButton(
            text="📥 Keep without a task",
            callback_data=DocAction(attachment_id=attachment.id, task_id=0).pack(),
        ),
    )
    return builder.as_markup()


def filed_text(attachment: Attachment, task: Task | None) -> str:
    name = attachment.file_name or ("photo" if attachment.kind == "photo" else "file")
    if task is None:
        return f"📥 <b>{escape(name)}</b> kept without a task."
    return f"📎 <b>{escape(name)}</b> filed under #{task.id} {escape(task.title)}."


def doc_caption(attachment: Attachment, task: Task | None) -> str:
    """What is written under a file that `/docs` sends back."""
    if task is None:
        return "📥 no task"
    project = "" if task.project is None else f" · #{escape(task.project)}"
    return f"#{task.id} {escape(task.title)}{project}"


# --- the pinned dashboard --------------------------------------------------

#: Section 12 keeps the pinned message under this.
DASHBOARD_LIMIT = 3000


def dashboard(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    board: Board,
    now: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    blocked: Mapping[int, list[int]] | None = None,
) -> str:
    """Today per owner, the two counts that matter, and the next three things."""
    grouped: dict[int, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(task.owner_id, []).append(task)

    blocks = [f"📌 <b>Today — {now.astimezone(tz):%a %d %b}</b>"]
    if grouped:
        for owner_id, owned in sorted(grouped.items(), key=lambda item: _short_of(owners, item[0])):
            rows = _rows(owned, now=now, tz=tz, blocked=blocked)
            blocks.append(f"<b>{escape(_short_of(owners, owner_id))}</b>\n{rows}")
    else:
        blocks.append(NOTHING_TODAY)

    counts = []
    if board.overdue:
        counts.append(f"⚠️ {board.overdue} overdue")
    if board.waiting:
        counts.append(f"⏳ {board.waiting} waiting")
    blocks.append(" · ".join(counts) if counts else "Nothing late, nothing waiting.")

    if board.upcoming:
        blocks.append("<b>Next up</b>\n" + _rows(board.upcoming, now=now, tz=tz, owners=owners))

    text = "\n\n".join(blocks)
    if len(text) > DASHBOARD_LIMIT:
        text = text[: DASHBOARD_LIMIT - 1].rstrip() + "…"
    return text


# --- the board -------------------------------------------------------------

BOARD_EMPTY = "📊 <b>Tracker</b>\n\nNothing open and nothing closed today. Enjoy it."

_DONE_MARK = "▰"
_TODO_MARK = "▱"
_BAR_MARK = "█"
_BAR_WIDTH = 10


def board(data: Board, *, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """The whole picture in one message: progress today, the week, who, what."""
    if data.is_empty:
        return BOARD_EMPTY

    local = data.at.astimezone(tz)
    blocks = [
        f"📊 <b>Tracker — {local:%a %d %b %H:%M}</b>",
        _today_block(data),
        _week_block(data, today=local.date()),
        _people_block(data),
        _projects_block(data),
        _upcoming_block(data, now=data.at, tz=tz),
    ]
    return "\n\n".join(block for block in blocks if block)


def _today_block(data: Board) -> str:
    total = data.today_total
    bar = progress_bar(data.done_today, total)
    headline = (
        f"{bar}  {data.done_today} of {total} done today" if total else f"{bar}  nothing due today"
    )
    counters = [f"📦 {data.open_total} open"]
    if data.overdue:
        counters.insert(0, f"⚠️ {data.overdue} overdue")
    if data.waiting:
        counters.insert(-1, f"⏳ {data.waiting} waiting")
    return f"{headline}\n{' · '.join(counters)}"


def _week_block(data: Board, *, today: date) -> str:
    if not data.days:  # pragma: no cover - always seven days
        return ""
    busiest = max(day.count for day in data.days)
    if busiest == 0:
        return "<b>The week ahead</b>\nNothing dated in the next seven days."
    lines = []
    for day in data.days:
        label = "today" if day.day == today else f"{day.day:%a %d}"
        bar = volume_bar(day.count, busiest) if day.count else "·"
        lines.append(f"{label:<8}{bar} {day.count or ''}".rstrip())
    return "<b>The week ahead</b>\n<pre>" + "\n".join(lines) + "</pre>"


def _people_block(data: Board) -> str:
    if len(data.people) < 2:
        return ""
    lines = []
    for person in data.people:
        total = person.due_today + person.done_today
        bar = progress_bar(person.done_today, total, width=6)
        tail = f"{person.done_today}/{total} today" if total else "clear today"
        if person.overdue:
            tail += f" · {person.overdue} late"
        if person.waiting:
            tail += f" · {person.waiting} waiting"
        lines.append(f"{person.user.short[:10]:<11}{bar}  {tail}")
    return "<b>Who is carrying what</b>\n<pre>" + escape("\n".join(lines)) + "</pre>"


def _projects_block(data: Board) -> str:
    if not data.projects:
        return ""
    biggest = max(project.count for project in data.projects)
    lines = [
        f"{(project.project or 'no project')[:14]:<15}"
        f"{volume_bar(project.count, biggest)} {project.count}"
        for project in data.projects[:6]
    ]
    return "<b>Open by project</b>\n<pre>" + escape("\n".join(lines)) + "</pre>"


def _upcoming_block(data: Board, *, now: datetime, tz: ZoneInfo) -> str:
    if not data.upcoming:
        return ""
    rows = _rows(data.upcoming, now=now, tz=tz)
    return f"<b>Next up</b>\n{rows}"


def progress_bar(done: int, total: int, *, width: int = _BAR_WIDTH) -> str:
    """`▰▰▰▱▱▱▱▱▱▱` — how much of today is behind you."""
    if total <= 0:
        return _TODO_MARK * width
    filled = round(width * done / total)
    return _DONE_MARK * filled + _TODO_MARK * (width - filled)


def volume_bar(count: int, biggest: int, *, width: int = _BAR_WIDTH) -> str:
    """A bar scaled against the busiest row, so the shape is readable at a glance."""
    if biggest <= 0 or count <= 0:
        return ""
    return _BAR_MARK * max(1, round(width * count / biggest))


def _when(due: datetime, *, now: datetime, tz: ZoneInfo) -> str:
    """`14:30` for today, `⚠️ Mon 14 Sep 09:00` when it is already late."""
    local_due = due.astimezone(tz)
    local_now = now.astimezone(tz)
    if local_due < local_now:
        return f"⚠️ {local_due:%a %d %b %H:%M}"
    if local_due.date() == local_now.date():
        return f"{local_due:%H:%M}"
    return f"{local_due:%a %d %b %H:%M}"


def _short_of(owners: Mapping[int, User], owner_id: int) -> str:
    owner = owners.get(owner_id)
    return owner.short if owner is not None else f"user {owner_id}"
