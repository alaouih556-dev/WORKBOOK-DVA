from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .security import hash_token, new_secret_token


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _order_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _is_unique_violation(err: sqlite3.Error) -> bool:
    return "UNIQUE" in str(err).upper() or "PRIMARY KEY" in str(err).upper()


def insert_order(conn: sqlite3.Connection, data: dict[str, str]) -> dict[str, Any]:
    now = utcnow_iso()
    order_id = "ord_" + uuid.uuid4().hex[:24]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    order_number = f"DVA-{stamp}-{uuid.uuid4().hex[:6].upper()}"
    checkout_token = new_secret_token(32)
    conn.execute(
        """
        INSERT INTO orders (
            id, order_number, first_name, last_name, email, phone, company,
            challenge, status, checkout_token_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
        """,
        (
            order_id,
            order_number,
            data["firstName"].strip(),
            data["lastName"].strip(),
            data["email"].strip().lower(),
            data["phone"].strip(),
            data["company"].strip(),
            data["challenge"].strip(),
            hash_token(checkout_token),
            now,
            now,
        ),
    )
    order = _order_to_dict(conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone())
    order["checkout_token"] = checkout_token
    return order


def get_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    return _order_to_dict(row) if row else None


def set_order_checkout(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    transaction_id: str,
    price_id: str,
    currency: str,
) -> None:
    conn.execute(
        """
        UPDATE orders
        SET status = 'checkout_started',
            paddle_transaction_id = ?,
            paddle_price_id = ?,
            currency = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (transaction_id, price_id, currency, utcnow_iso(), order_id),
    )


def update_order_paid(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    transaction_id: str,
    price_id: str,
    currency: str,
    paid_at: str,
) -> None:
    conn.execute(
        """
        UPDATE orders
        SET status = 'paid',
            paddle_transaction_id = COALESCE(paddle_transaction_id, ?),
            paddle_price_id = COALESCE(paddle_price_id, ?),
            currency = COALESCE(currency, ?),
            paid_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (transaction_id, price_id, currency, paid_at, utcnow_iso(), order_id),
    )


def set_delivery(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    status: str,
    attempts: int,
    error: str | None = None,
    delivered_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE orders
        SET delivery_status = ?,
            delivery_error = ?,
            delivery_attempts = ?,
            delivered_at = COALESCE(?, delivered_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            error,
            attempts,
            delivered_at,
            utcnow_iso(),
            order_id,
        ),
    )


def insert_webhook_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    notification_id: str | None,
    occurred_at: str | None,
    payload: dict[str, Any],
    outcome: str,
) -> bool:
    """Insert the event row. Returns True if newly inserted, False if already
    present (duplicate delivery of the same event_id)."""
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO webhook_events (
                event_id, event_type, notification_id, occurred_at,
                payload, outcome, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_type,
                notification_id,
                occurred_at,
                json.dumps(payload, ensure_ascii=False),
                outcome,
                utcnow_iso(),
            ),
        )
    except sqlite3.Error as exc:
        if _is_unique_violation(exc):
            return False
        raise
    return conn.execute(
        "SELECT changes()"
    ).fetchone()[0] == 1


def set_event_outcome(
    conn: sqlite3.Connection,
    event_id: str,
    outcome: str,
) -> None:
    conn.execute(
        "UPDATE webhook_events SET outcome = ? WHERE event_id = ?",
        (outcome, event_id),
    )


def get_webhook_event(conn: sqlite3.Connection, event_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM webhook_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------
# Admin stats
# ------------------------------------------------------------------

def order_stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid,
            SUM(CASE WHEN delivery_status = 'sent' THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN delivery_status = 'pending' THEN 1 ELSE 0 END) AS pending_delivery
        FROM orders
        """
    ).fetchone()
    return {
        "orders_total": rows["total"] or 0,
        "orders_paid": rows["paid"] or 0,
        "orders_delivered": rows["delivered"] or 0,
        "orders_pending_delivery": rows["pending_delivery"] or 0,
    }


def recent_orders(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT order_number, status, delivery_status, paid_at, created_at, email
        FROM orders
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_pending_deliveries(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE delivery_status = 'pending'"
    ).fetchone()
    return row["n"] or 0