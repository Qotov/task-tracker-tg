# IDEAS

Things noticed while building, deliberately not built. Nothing here is in
`docs/TASK.md`; move an item into the spec before implementing it.

- Let each person set their own `short` and display name from `/settings`, instead of deriving the handle from the Telegram username once and freezing it.
- A **nudge** button that pings the other person about a task they own. It is a notification, so it belongs after Phase 3 builds the quiet-hours outbox — nothing may reach anybody at 23:00.
- Pull French public holidays from `api.gouv.fr` instead of computing them, if a regional holiday (Alsace-Moselle, the DOM) ever matters. The computed table has no network to fail.
- Fetch the `<title>` of a URL in a task so a card reads "Demande de titre de séjour — service-public.fr" instead of a bare link. Needs an HTTP call, a timeout, and a mocked test.
- Warn when a due date lands the day after a holiday, when every office has a queue.
- A weekly "who did what" summary, once the digest exists in Phase 3.
- Accept French date words (`demain`, `lundi`, `14h30`) alongside the English ones — the paperwork this bot tracks is French, and so is half the household's typing.
- Accept looser phrasing such as "in 2 days", "next week", "end of the month".
- Answer with a hint when an `@mention` is one edit away from a known short (`@alexx`), instead of silently leaving it in the title.
- An `/undo` that deletes the task just created, for when a chatty group message becomes a task by accident.
- A way to mark a plain group message as "not a task" — for example, ignore messages that end in a question mark.
- Index `tasks(status, due_at)` if the listing queries ever get slow; at two users and a few hundred rows they do not need it.
- Wire `pytest-cov` into `make test` to enforce the 80% target from section 18 mechanically rather than by inspection.
- Ship a `justfile` or shell completion for the commands, if the two of us ever run the bot locally for debugging.
