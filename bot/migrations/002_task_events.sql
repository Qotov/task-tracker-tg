-- 002: a log of what happened to each task.
--
-- The tasks table only holds the present. "Am I keeping up?", "what did I get
-- done last week?" and "when do I let things slip?" all need the past, and
-- done_at is cleared the moment somebody reopens a task, so history cannot be
-- reconstructed from it afterwards.

CREATE TABLE task_events (
  id       INTEGER PRIMARY KEY,
  task_id  INTEGER NOT NULL,
  kind     TEXT NOT NULL CHECK (kind IN
             ('created','done','reopened','dropped','waiting','rescheduled')),
  at       TEXT NOT NULL,   -- UTC ISO 8601
  actor_id INTEGER          -- who did it, when we know
);

CREATE INDEX task_events_at_idx ON task_events (at);
CREATE INDEX task_events_task_idx ON task_events (task_id, at);

-- Backfill everything the current rows can still tell us, so the first /stats
-- after this migration is not empty.
INSERT INTO task_events (task_id, kind, at, actor_id)
  SELECT id, 'created', created_at, created_by FROM tasks;

INSERT INTO task_events (task_id, kind, at, actor_id)
  SELECT id, 'done', done_at, owner_id FROM tasks WHERE done_at IS NOT NULL;
