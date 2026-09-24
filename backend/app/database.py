from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id                   TEXT PRIMARY KEY,
    order_number         TEXT NOT NULL UNIQUE,
    first_name           TEXT NOT NULL,
    last_name            TEXT NOT NULL,
    email                TEXT NOT NULL,
    phone                TEXT NOT NULL,
    company              TEXT NOT NULL,
    challenge            TEXT NOT NULL,
    status               TEXT NOT NULL,
    checkout_token_hash  TEXT NOT NULL,
    paddle_transaction_id TEXT,
    paddle_price_id      TEXT,
    currency             TEXT,
    delivery_status      TEXT,
    delivery_error       TEXT,
    delivery_attempts    INTEGER NOT NULL DEFAULT 0,
    paid_at              TEXT,
    delivered_at         TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    notification_id TEXT,
    occurred_at     TEXT,
    payload         TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    processed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_transaction ON orders (paddle_transaction_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
"""


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class Database:
    """Small wrapper so routes can open short-lived connections easily."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager that yields a connection, commits on success,
        rolls back on error and always closes it."""
        conn = connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)