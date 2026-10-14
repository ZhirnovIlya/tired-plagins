"""Хранилище шлюза (SQLite): принятые события и соответствие аккаунт площадки → арендатор TIREK.

Персональных данных здесь нет: только id событий, id аккаунтов (компаний) и id арендаторов.
"""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    result      TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id    TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    last_event_at TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def seen(self, event_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return row is not None

    def record(self, event_id: str, type_: str, account_id: str, result: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO events (event_id, type, account_id, result)"
                " VALUES (?,?,?,?)",
                (event_id, type_, account_id, result),
            )

    def account(self, account_id: str) -> tuple[str | None, datetime | None]:
        with self._db() as db:
            row = db.execute(
                "SELECT tenant_id, last_event_at FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
        if row is None:
            return None, None
        return row[0], datetime.fromisoformat(row[1]) if row[1] else None

    def save_account(self, account_id: str, tenant_id: str, at: datetime | None = None) -> None:
        with self._db() as db:
            db.execute(
                """INSERT INTO accounts (account_id, tenant_id, last_event_at) VALUES (?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET tenant_id = excluded.tenant_id,
                   last_event_at = COALESCE(excluded.last_event_at, accounts.last_event_at)""",
                (account_id, tenant_id, at.astimezone(UTC).isoformat() if at else None),
            )
