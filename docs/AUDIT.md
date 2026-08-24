# Product audit

Written against the working implementation, not against intentions. Every defect
below was reproduced before it was believed, and the fixed ones have a test that
fails without the fix.

## What the product is, and what that rules out

Two people, one private group, one SQLite file, everything inside Telegram. That
frame is not a limitation to design around — it is the reason the thing gets used
at all, because there is no app to open. Three rules in `CLAUDE.md` are marked as
never to be broken and shape everything here: **no priority field**, **exactly one
owner per task**, **no notification inside quiet hours**.

A request to "add priorities" therefore cannot be honoured without an explicit
decision to change the product's premise. See *Open questions* at the end.

## Defects found, with evidence

### 1. The same notification could be sent twice — FIXED

`tick` (every 60s) and `digest_round` (hourly, at :00) both call `flush_outbox`.
Reading the unsent rows and marking them sent were separated by an `await` on the
network, so at the top of every hour two flushes overlapped:

```
messages queued : 1
messages sent   : 2 -> ['⏰ your reminder', '⏰ your reminder']
```

Fixed by claiming each row with a conditional `UPDATE … WHERE sent_at IS NULL`
and checking `rowcount`, so exactly one caller may send it. A failed send hands
the claim back, so the next tick retries rather than dropping it.

### 2. A double tap could roll a recurring task twice — FIXED

`complete_task` read the status, checked it, then wrote — three statements with
gaps. Two processes (which really did run twice during this project) could both
believe they closed the task and each create a next instance. Now one conditional
write decides, and the loser is told `already_done`.

### 3. "Say this once" was two statements — FIXED

`already_said` followed by `remember_said` has the same shape. The primary key on
`notifications_sent` makes it atomic instead: `INSERT OR IGNORE` and check
`rowcount`. Applies to reminders, overdue pings, escalations and unblock
announcements.

### 4. Statistics could not answer the questions asked of them — FIXED

The `tasks` table holds only the present, and `done_at` is wiped the moment
anybody reopens a task. "Am I keeping up?", "what did I finish last week?" and
"when do I slip?" were unanswerable, and any trend built on `done_at` would have
quietly lied after the first reopen.

Migration `002_task_events.sql` adds an append-only log of state changes and
backfills what the current rows can still prove. `/stats` is built on it.

### 5. A failed model call was invisible — FIXED (earlier)

The first live Gemini call returned 404 and the only symptom was a card that
never changed. `/health` now names the failure and the reason.

### 6. A notification did not look like a notification — FIXED (earlier)

A reminder arrived between a button press and its answer, wearing the same ⚠️ as
the overdue list, and was read as part of the reply. Unprompted messages now open
by naming themselves and carry the task's buttons.

## Weaknesses not yet addressed

Ordered by what a daily user would feel first.

| # | Problem | Why it matters |
| --- | --- | --- |
| 1 | **No search.** `/docs` finds attachments; nothing finds a task by word. | Past ~50 tasks the only way to find one is to scroll a list. |
| 2 | **Lists do not paginate.** Every open task is printed; only the first 8 get a button. | A 60-task `/mine` is a wall with no way to act on task 30. |
| 3 | **No delete.** `/drop` hides a task but it stays for ever. | A typo task is permanent. |
| 4 | **No task detail view.** A card shows blockers and notes but not its subtasks. | Subtasks are invisible unless you remember they exist. |
| 5 | **The group cannot be re-bound.** First group wins, permanently. | Re-creating the group means editing the database by hand. |
| 6 | **`N+1` queries** in `blocked_map` and the tick's `is_blocked`. | Invisible at two users; the first thing to hurt at scale. |
| 7 | **No weekly review.** The digest is daily; nothing summarises a week. | The spec's own "three features that carry the value" logic argues for it. |
| 8 | **No undo for a destructive edit.** Reopen exists; a wrong `/due` or `/own` does not. | The event log now makes this buildable. |
| 9 | **Templates (phase 5) absent**, and `/new` collides with the spec's name for them. | The one unbuilt phase. |

## Open questions for the owner

1. **Priorities.** Requested in the brief, forbidden by invariant 1 ("the due date
   carries all urgency"). I have not added them. Reversing that is a product
   decision, not a code change — say the word and it becomes a migration, a
   parser marker, a sort key and a card line.
2. **More than two people.** `partner_of`, the "Give to" button and the digest all
   assume exactly two. Opening that up is a real redesign, not a setting.
3. **A Mini App.** Telegram's web view would give real charts and a table. It also
   means a hosted web asset, HTTPS and a second UI to keep in step — against the
   "no web interface" non-goal. Worth it only if the text views stop being enough.
