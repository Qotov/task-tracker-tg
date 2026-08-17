"""Every string the bot sends, built here in Telegram-flavoured HTML.

Handlers never format anything themselves. Times arrive as UTC and are rendered in
the display timezone; task ids are always shown as `#12`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from bot.parser import PARIS
from bot.services.tasks import Task
from bot.services.users import User

NOTHING_TODAY = "Nothing due today. 🎉"
NOTHING_OPEN = "No open tasks here. 🎉"
UNKNOWN_TASK = "There is no task <b>#{task_id}</b>."
BAD_TASK_REF = "Give me a task id, like <code>/done 12</code>."


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
        "<b>Commands</b>\n"
        "/add &lt;text&gt; — add a task (same parsing as a plain message)\n"
        "/today — everything due today or earlier, both of us\n"
        "/mine — your open tasks\n"
        "/done &lt;id&gt; — close a task, for example <code>/done 12</code> "
        "or <code>/done #12</code>\n"
        "/help — this message\n"
        "/start — register yourself and open a direct chat with me"
    )


def task_card(task: Task, owner: User, *, now: datetime, tz: ZoneInfo = PARIS) -> str:
    """The block shown after a task is created or changed."""
    lines = [f"#{task.id} <b>{escape(task.title)}</b>"]
    details = [f"👤 {escape(owner.short)}"]
    if task.due_at is not None:
        details.append(f"📅 {_when(task.due_at, now=now, tz=tz)}")
    if task.project is not None:
        details.append(f"🏷 #{escape(task.project)}")
    lines.append(" · ".join(details))
    return "\n".join(lines)


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


def _row(task: Task, *, now: datetime, tz: ZoneInfo) -> str:
    line = f"• #{task.id} {escape(task.title)}"
    bits: list[str] = []
    if task.due_at is not None:
        bits.append(_when(task.due_at, now=now, tz=tz))
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
