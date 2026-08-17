"""Every string and every keyboard the bot sends, built here.

Handlers never format anything themselves. Times arrive as UTC and are rendered in
the display timezone; task ids are always shown as `#12`. Callback data follows
section 13: a short prefix, an action, and the task id — `t:done:12`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.parser import PARIS
from bot.services.tasks import Task
from bot.services.users import User

NOTHING_TODAY = "Nothing due today. 🎉"
NOTHING_OPEN = "No open tasks here. 🎉"
NOTHING_OVERDUE = "Nothing overdue. 🎉"
NOTHING_THIS_WEEK = "Nothing due in the next seven days."
UNKNOWN_TASK = "There is no task <b>#{task_id}</b>."
BAD_TASK_REF = "Give me a task id, like <code>/done 12</code>."
NO_PARTNER = "Nobody else has started the bot yet."

SUBTASK_PROMPT = "What is the subtask of #{task_id}? Send the title in your next message."
SUBTASK_EXPIRED = (
    "That subtask prompt was more than five minutes old, so I added this as a normal task."
)


class TaskAction(CallbackData, prefix="t"):
    """Button payload: renders as `t:done:12`."""

    action: str
    task_id: int


def start_text(user: User) -> str:
    return (
        f"Hello {escape(user.display_name)}, you are registered "
        f"as <b>@{escape(user.short)}</b>.\n\n"
        "Write anything in the group and it becomes a task. Markers you can use:\n"
        "<b>@name</b> owner · <b>#project</b> project · a date like "
        "<code>tomorrow</code>, <code>20/09</code>, <code>+3d</code>, <code>fri 18:00</code>.\n\n"
        "Try <code>/help</code> for the command list."
    )


def help_text() -> str:
    """The commands that exist today. Later phases add to this list."""
    return (
        "<b>What I understand</b>\n\n"
        "Any plain message becomes a task. In the text:\n"
        "· <b>@name</b> or <b>@me</b> — who owns it (exactly one person)\n"
        "· <b>#project</b> — the project\n"
        "· a date — <code>today</code>, <code>tomorrow</code>, <code>mon</code>…<code>sun</code>, "
        "<code>20/09</code>, <code>20.09</code>, <code>2026-09-20</code>, <code>+3d</code>, "
        "<code>+2w</code>, <code>+1m</code>, any of them with a time like <code>14:30</code>\n"
        "A date without a time means 09:00. There are no priorities: "
        "the due date is the urgency.\n\n"
        "Every task card has buttons, so you rarely need a command at all.\n\n"
        "<b>Commands</b>\n"
        "/add &lt;text&gt; — add a task\n"
        "/sub &lt;id&gt; &lt;text&gt; — add a subtask under it\n"
        "/done &lt;id&gt; — close a task\n"
        "/drop &lt;id&gt; — abandon a task\n"
        "/due &lt;id&gt; &lt;date&gt; — change the due date\n"
        "/own &lt;id&gt; @who — hand it over\n"
        "/note &lt;id&gt; &lt;text&gt; — append a dated note\n"
        "/today — due today or earlier, both of us\n"
        "/week — the next seven days\n"
        "/overdue — everything past due\n"
        "/mine — your open tasks\n"
        "/help — this message"
    )


# --- keyboards -------------------------------------------------------------


def task_keyboard(task: Task, *, partner: User | None = None) -> InlineKeyboardMarkup | None:
    """Section 13: a todo card and a waiting card carry buttons, a closed one does not."""
    builder = InlineKeyboardBuilder()
    if task.status == "todo":
        builder.row(
            _button("✅ Done", "done", task.id),
            _button("📅 +1 day", "day1", task.id),
        )
        second: list[InlineKeyboardButton] = []
        if partner is not None:
            second.append(_button(f"👤 Give to {partner.short}", "give", task.id))
        second.append(_button("➕ Subtask", "sub", task.id))
        second.append(_button("⏳ Waiting", "wait", task.id))
        builder.row(*second)
        return builder.as_markup()
    if task.status == "waiting":
        builder.row(
            _button("✅ Done", "done", task.id),
            _button("📅 +7 days", "day7", task.id),
            _button("↩️ Back to todo", "todo", task.id),
        )
        return builder.as_markup()
    return None


def _button(text: str, action: str, task_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=TaskAction(action=action, task_id=task_id).pack()
    )


# --- cards -----------------------------------------------------------------


def task_card(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = PARIS) -> str:
    """The block shown after a task is created or changed."""
    lines = [f"{_status_mark(task)}#{task.id} <b>{escape(task.title)}</b>"]

    details = [f"👤 {escape(owner.short)}"]
    if task.due_at is not None:
        details.append(f"📅 {_when(task.due_at, now=now, tz=tz)}")
    if task.project is not None:
        details.append(f"🏷 #{escape(task.project)}")
    lines.append(" · ".join(details))

    if task.status == "waiting" and task.follow_up_at is not None:
        lines.append(f"⏳ waiting — chase it up {_when(task.follow_up_at, now=now, tz=tz)}")
    if task.parent_id is not None:
        lines.append(f"↳ subtask of #{task.parent_id}")
    if task.notes:
        lines.append("📝 " + escape(task.notes).replace("\n", "\n   "))
    return "\n".join(lines)


def _status_mark(task: Task) -> str:
    return {"done": "✅ ", "dropped": "🗑 ", "waiting": "⏳ "}.get(task.status, "")


def created(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = PARIS) -> str:
    return "✍️ Added\n" + task_card(task, owner, now=now, tz=tz)


def completed(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = PARIS) -> str:
    return "✅ Done\n" + task_card(task, owner, now=now, tz=tz)


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
    tz: ZoneInfo = PARIS,
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
        rows = "\n".join(_row(task, now=now, tz=tz) for task in owned)
        blocks.append(f"<b>{escape(name)}</b>\n{rows}")
    return f"{header}\n\n" + "\n\n".join(blocks)


def week_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = PARIS,
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
        rows = "\n".join(_row(task, now=now, tz=tz, owners=owners, with_time=True) for task in due)
        blocks.append(f"<b>{when:%a %d %b}</b>\n{rows}")
    return f"{header}\n\n" + "\n\n".join(blocks)


def overdue_list(
    tasks: Iterable[Task],
    owners: Mapping[int, User],
    *,
    now: datetime,
    tz: ZoneInfo = PARIS,
) -> str:
    """Everything past due, oldest first."""
    rows = [_row(task, now=now, tz=tz, owners=owners) for task in tasks]
    header = "<b>Overdue</b>"
    if not rows:
        return f"{header}\n\n{NOTHING_OVERDUE}"
    return f"{header}\n" + "\n".join(rows)


def open_list(
    tasks: Iterable[Task],
    *,
    title: str,
    now: datetime,
    tz: ZoneInfo = PARIS,
) -> str:
    """A flat list, used by /mine."""
    rows = [_row(task, now=now, tz=tz) for task in tasks]
    if not rows:
        return f"<b>{escape(title)}</b>\n\n{NOTHING_OPEN}"
    return f"<b>{escape(title)}</b>\n" + "\n".join(rows)


def _row(
    task: Task,
    *,
    now: datetime,
    tz: ZoneInfo,
    owners: Mapping[int, User] | None = None,
    with_time: bool = False,
) -> str:
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
