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

### 7. Everything past a screenful was unreachable — FIXED

Nine weaknesses were listed here after the first pass. Eight are now closed.

| # | Problem | Resolution |
| --- | --- | --- |
| 1 | **No search.** `/docs` found attachments; nothing found a task by word. | `/find <word>` and a 🔍 Find button search titles, projects and notes, closed tasks included and listed last. |
| 2 | **Lists did not paginate.** Every task printed; only the first 8 had a button. | Ten rows a page with ◀ ▶ arrows. A stale button asking for page 99 lands on the last page rather than an empty one. |
| 3 | **No delete.** `/drop` hid a task for ever. | 🗑 Delete on a closed card, behind a confirmation, taking subtasks, links, notifications and history with it. Attachments survive. |
| 4 | **Subtasks invisible on the card.** | A card shows `▰▰▰▱▱▱ 1/2 subtasks`, in one query for a whole list. |
| 5 | **The group could not be re-bound.** | `/group` claims the current group; the middleware lets that one command through an unclaimed group, or it could never be heard where it is needed. |
| 6 | **`N+1` queries** in `blocked_map` and the tick. | One query per list and one per tick. |
| 7 | **No weekly review.** | Sunday at each person's digest hour: the week's figures and what they mean, held for quiet hours like everything else. |
| 8 | **No undo for a destructive edit.** | *Still open.* Reopen and the delete confirmation cover the dangerous cases; a wrong `/due` is one more press to correct. The event log makes real undo buildable when it earns its place. |
| 9 | **Templates (phase 5).** | *Out of scope by instruction* — the owner asked for every phase except this one. `/new` still collides with the spec's name for it; `/from` is the suggested rename. |

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
