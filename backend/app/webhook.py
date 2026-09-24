from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from .config import Settings
from .database import Database
from .models import (
    insert_webhook_event,
    get_order,
    set_event_outcome,
    update_order_paid,
    utcnow_iso,
)

logger = logging.getLogger(__name__)


def parse_signature_header(header: str) -> tuple[int | None, list[str]]:
    """Extract ``ts`` and all ``h1`` values from the Paddle-Signature header.

    Paddle may rotate secrets and include more than one ``h1``, in which case
    a match against any of them is accepted.
    """
    ts: int | None = None
    hashes: list[str] = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "ts":
            try:
                ts = int(value)
            except ValueError:
                ts = None
        elif key == "h1":
            hashes.append(value)
    return ts, hashes


def is_signature_valid(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    max_age_seconds: int,
    now: float | None = None,
) -> bool:
    """Verify the Paddle-Signature over the RAW request body.

    Algorithm (official docs): HMAC-SHA256 of ``"{ts}:{raw_body}"`` compared
    against ``h1``. The body must not be parsed/re-serialized before this
    check, otherwise the signature never matches.
    """
    if not signature_header or not secret:
        return False
    ts, hashes = parse_signature_header(signature_header)
    if ts is None or not hashes:
        return False

    now = now if now is not None else time.time()
    age = now - ts
    # Reject replayed/expired events but tolerate small clock skew.
    if age > max_age_seconds or age < -30:
        return False

    try:
        body = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return False

    signed_payload = f"{ts}:{body}"
    for h1 in hashes:
        expected = hmac.new(
            secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, h1):
            return True
    return False


def configured_price_in_items(items: list[Any] | None, price_id: str) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        price = (item or {}).get("price") or {}
        if price.get("id") == price_id and int((item or {}).get("quantity", 1)) >= 1:
            return True
    return False


def process_paddle_event(
    db: Database,
    payload: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Idempotent processing of a verified webhook payload. Must be called
    only after the signature has been validated."""
    event_id = payload.get("event_id")
    event_type = payload.get("event_type") or ""
    entity = payload.get("data") or {}

    with db.connection() as conn:
        inserted = insert_webhook_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            notification_id=payload.get("notification_id"),
            occurred_at=payload.get("occurred_at"),
            payload=payload,
            outcome="processing",
        )
        if not inserted:
            # Duplicate delivery of an event we already saw: acknowledge,
            # never re-process, never re-deliver.
            return {
                "duplicate": True,
                "event_id": event_id,
                "event_type": event_type,
            }

        if event_type != "transaction.completed":
            set_event_outcome(conn, event_id, "ignored")
            return {"handled": True, "ignored": True, "event_id": event_id}

        # --- Consistency checks before marking paid ---
        txn_id = entity.get("id")
        custom_data = entity.get("custom_data") or {}
        order_id = custom_data.get("order_id")

        if not order_id:
            set_event_outcome(conn, event_id, "rejected:missing-order-ref")
            return {"handled": True, "rejected": "missing-order-ref", "event_id": event_id}

        order = get_order(conn, order_id)
        if not order:
            set_event_outcome(conn, event_id, "rejected:unknown-order")
            return {"handled": True, "rejected": "unknown-order", "event_id": event_id}

        if order["paddle_transaction_id"] and order["paddle_transaction_id"] != txn_id:
            set_event_outcome(conn, event_id, "rejected:transaction-mismatch")
            return {
                "handled": True,
                "rejected": "transaction-mismatch",
                "event_id": event_id,
            }

        if entity.get("status") != "completed":
            set_event_outcome(conn, event_id, "rejected:status-not-completed")
            return {
                "handled": True,
                "rejected": "status-not-completed",
                "event_id": event_id,
            }

        if not configured_price_in_items(
            entity.get("items"), settings.paddle_price_id
        ):
            set_event_outcome(conn, event_id, "rejected:price-mismatch")
            return {
                "handled": True,
                "rejected": "price-mismatch",
                "event_id": event_id,
            }

        currency = entity.get("currency_code")
        totals_currency = (
            (entity.get("details") or {}).get("totals") or {}
        ).get("currency_code")
        if currency != settings.paddle_expected_currency or (
            totals_currency and totals_currency != settings.paddle_expected_currency
        ):
            set_event_outcome(conn, event_id, "rejected:currency-mismatch")
            return {
                "handled": True,
                "rejected": "currency-mismatch",
                "event_id": event_id,
            }

        if settings.paddle_expected_amount_minor:
            grand_total = (
                (entity.get("details") or {}).get("totals") or {}
            ).get("grand_total")
            if grand_total != settings.paddle_expected_amount_minor:
                set_event_outcome(conn, event_id, "rejected:amount-mismatch")
                return {
                    "handled": True,
                    "rejected": "amount-mismatch",
                    "event_id": event_id,
                }

        update_order_paid(
            conn,
            order_id,
            transaction_id=txn_id,
            price_id=settings.paddle_price_id,
            currency=settings.paddle_expected_currency,
            paid_at=utcnow_iso(),
        )
        set_event_outcome(conn, event_id, "order-paid")

    # Delivery runs AFTER the database transaction commits, so a crash can
    # never leave a paid order with an un-committed event row.
    from .delivery import deliver

    delivery_result = deliver(db, order_id, settings)

    logger.info(
        "paddle_webhook processed event=%s type=%s paid=True delivery=%s",
        event_id,
        event_type,
        delivery_result.get("status"),
    )
    return {
        "handled": True,
        "paid": True,
        "event_id": event_id,
        "delivery": delivery_result,
    }


async def webhook_handler(
    raw_body: bytes,
    signature_header: str,
    settings: Settings,
    db: Database,
) -> tuple[int, dict[str, Any]]:
    if not signature_header:
        return 400, {"error": "missing_signature"}
    if not is_signature_valid(
        raw_body, signature_header, settings.paddle_webhook_secret,
        settings.webhook_max_age_seconds,
    ):
        return 401, {"error": "invalid_signature"}
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 400, {"error": "invalid_payload"}
    result = process_paddle_event(db, payload, settings)
    return 200, result