-- 001_init: the whole schema, section 5 of docs/TASK.md.
-- The migration runner keeps `schema_version`; migrations never touch it.

CREATE TABLE users (
  telegram_id  INTEGER PRIMARY KEY,
  short        TEXT NOT NULL UNIQUE,   -- used for @mentions, lowercase
  display_name TEXT NOT NULL,
  dm_chat_id   INTEGER,                -- filled when the user first DMs the bot
  digest_hour  INTEGER NOT NULL DEFAULT 8,
  quiet_start  TEXT NOT NULL DEFAULT '21:00',
  quiet_end    TEXT NOT NULL DEFAULT '07:30',
  escalation   INTEGER NOT NULL DEFAULT 0   -- 0 off, 1 on
);

CREATE TABLE tasks (
  id           INTEGER PRIMARY KEY,
  parent_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  owner_id     INTEGER NOT NULL REFERENCES users(telegram_id),
  status       TEXT NOT NULL CHECK (status IN ('todo','waiting','done','dropped')),
  project      TEXT,
  due_at       TEXT,      -- UTC ISO 8601
  remind_at    TEXT,      -- UTC ISO 8601
  follow_up_at TEXT,      -- only for status = 'waiting'
  recurrence   TEXT,      -- NULL, 'daily', 'weekly:mon', 'monthly:15', 'yearly:09-20'
  notes        TEXT,
  created_by   INTEGER NOT NULL REFERENCES users(telegram_id),
  created_at   TEXT NOT NULL,
  done_at      TEXT
);

CREATE TABLE task_deps (
  task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on_id)
);

CREATE TABLE attachments (
  id              INTEGER PRIMARY KEY,
  task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
  file_id         TEXT NOT NULL,
  file_unique_id  TEXT NOT NULL,
  file_name       TEXT,
  mime            TEXT,
  kind            TEXT NOT NULL CHECK (kind IN ('document','photo')),
  caption         TEXT,
  added_by        INTEGER NOT NULL REFERENCES users(telegram_id),
  added_at        TEXT NOT NULL
);

CREATE TABLE notifications_sent (
  task_id  INTEGER NOT NULL,
  kind     TEXT NOT NULL,   -- 'remind','overdue','unblocked','followup','escalation'
  day      TEXT NOT NULL,   -- Paris date, YYYY-MM-DD
  PRIMARY KEY (task_id, kind, day)
);

CREATE TABLE outbox (
  id         INTEGER PRIMARY KEY,
  chat_id    INTEGER NOT NULL,
  text       TEXT NOT NULL,
  keyboard   TEXT,          -- JSON, may be NULL
  send_after TEXT NOT NULL, -- UTC ISO 8601
  sent_at    TEXT
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
