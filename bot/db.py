"""SQLite access: connection, migration runner, and small query helpers.

No ORM by decision (section 3). Everything above this module speaks in plain rows
and dataclasses. Calls are synchronous: with two users the queries are
sub-millisecond, and a thread pool would buy nothing but complexity.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d+)_.*\.sql$")

Params = Sequence[Any]


def to_iso(moment: datetime | None) -> str | None:
    """Serialise an aware datetime as UTC ISO 8601 text, the only storage format.

    Microseconds are dropped so every stored timestamp has the same shape and can
    be compared and ordered as plain text in SQL.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise ValueError("refusing to store a naive datetime; attach a timezone first")
    return moment.astimezone(UTC).replace(microsecond=0).isoformat()


def from_iso(text: str | None) -> datetime | None:
    """Read a stored timestamp back as an aware UTC datetime."""
    if text is None:
        return None
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class Database:
    """A thin wrapper around one SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def connect(cls, path: Path) -> Database:
        """Open the database file, creating it with mode 0600 when it is new."""
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600)
            os.chmod(path, 0o600)
        return cls(sqlite3.connect(path, isolation_level=None))

    @classmethod
    def in_memory(cls) -> Database:
        """An empty in-memory database, for tests."""
        return cls(sqlite3.connect(":memory:", isolation_level=None))

    def close(self) -> None:
        self.connection.close()

    # --- queries -----------------------------------------------------------

    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def insert(self, sql: str, params: Params = ()) -> int:
        """Run an INSERT and return the new rowid."""
        cursor = self.connection.execute(sql, params)
        row_id = cursor.lastrowid
        if row_id is None:  # pragma: no cover - sqlite always sets it for INSERT
            raise RuntimeError("insert did not produce a rowid")
        return row_id

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    # --- migrations --------------------------------------------------------

    def schema_version(self) -> int:
        """The highest applied migration number, 0 on a fresh database."""
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self.connection.execute("SELECT version FROM schema_version").fetchone()
        return 0 if row is None else int(row["version"])

    def migrate(self, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
        """Apply every migration numbered above the stored version.

        Returns the numbers applied. Each file runs together with its version bump
        in one transaction, so an interrupted migration is never half-recorded.
        """
        current = self.schema_version()
        applied: list[int] = []
        for number, path in _pending(migrations_dir, current):
            logger.info("applying migration %s", path.name)
            body = path.read_text(encoding="utf-8").strip()
            if not body.endswith(";"):
                body += ";"
            # executescript() commits any open transaction first, so the BEGIN has
            # to live inside the script itself.
            self.connection.executescript(
                "BEGIN;\n"
                f"{body}\n"
                "DELETE FROM schema_version;\n"
                f"INSERT INTO schema_version (version) VALUES ({number});\n"
                "COMMIT;"
            )
            applied.append(number)
        return applied


def _pending(migrations_dir: Path, current: int) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            logger.warning("ignoring %s: name does not start with a migration number", path.name)
            continue
        number = int(match.group(1))
        if number > current:
            found.append((number, path))
    return sorted(found)
