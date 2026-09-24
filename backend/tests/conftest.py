from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app import paddle as paddle_module


# ---------------------------------------------------------------------------
# Sign helper – used to produce valid Paddle signatures in tests
# ---------------------------------------------------------------------------

def sign(raw: bytes, secret: str, *, ts: int | None = None) -> str:
    if ts is None:
        ts = int(time.time())
    signed = f"{ts}:{raw.decode()}"
    h1 = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


# ---------------------------------------------------------------------------
# Fake Paddle API client – avoids real HTTP calls
# ---------------------------------------------------------------------------

_FAKE_TXN_ID = "txn_test_paddle_abc123def456"
_FAKE_CHECKOUT = "https://sandbox.checkout.paddle.com/test?_ptxn=txn_test_paddle_abc123def456"

_LAST_TXN_CALL: dict | None = None


def _fake_create_transaction(settings, *, custom_data, quantity=1):
    global _LAST_TXN_CALL
    # Mirror the real guard in app/paddle.py: an empty API key or Price ID
    # must surface as PaymentNotConfigured (→ 503), never be masked by the fake.
    if not settings.paddle_api_key or not settings.paddle_price_id:
        raise paddle_module.PaymentNotConfigured(
            "PADDLE_API_KEY or PADDLE_PRICE_ID missing"
        )
    _LAST_TXN_CALL = {"custom_data": dict(custom_data), "quantity": quantity}

    async def _call():
        return {
            "transaction_id": _FAKE_TXN_ID,
            "checkout_url": _FAKE_CHECKOUT,
            "status": "draft",
        }
    return _call()


def last_create_transaction_call() -> dict | None:
    """Return the custom_data/quantity sent to the (fake) Paddle API on the
    most recent order creation, or None if nothing was sent yet."""
    return _LAST_TXN_CALL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def settings(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    wb_file = tmp_path / "workbook.pdf"
    wb_file.write_bytes(b"%PDF-1.4 fake workbook content")

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_sandbox_test_key_123")
    monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "test_clt_1234567890abcdef")
    monkeypatch.setenv("PADDLE_PRICE_ID", "pri_test_price_0000000000000000a")
    monkeypatch.setenv("PADDLE_EXPECTED_CURRENCY", "USD")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "pdl_ntfset_secret_for_testing_only")
    monkeypatch.setenv("ACCESS_LINK_SECRET", "test_signing_secret_abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost")
    monkeypatch.setenv("EMAIL_HOST", "")  # SMTP not configured → delivery pending
    monkeypatch.setenv("MASTERCLASS_URL", "https://zoom.us/j/123")
    monkeypatch.setenv("DELIVERY_FILE_WORKBOOK", str(wb_file))
    monkeypatch.setenv("ADMIN_STATUS_TOKEN", "admin_secret_token_123")
    monkeypatch.setenv("CORS_ORIGINS", "*")

    get_settings.cache_clear()
    yield get_settings()


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setattr(paddle_module, "create_transaction", _fake_create_transaction)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _db_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _count_events(settings) -> int:
    with _db_conn(settings.database_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()
        return row[0]


def _get_order(settings, order_id: str):
    with _db_conn(settings.database_path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def _get_event(settings, event_id: str):
    with _db_conn(settings.database_path) as conn:
        row = conn.execute("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def _make_completed_payload(
    order_id: str,
    *,
    txn_id: str = _FAKE_TXN_ID,
    price_id: str = "pri_test_price_0000000000000000a",
    currency: str = "USD",
    event_id: str = "evt_test_001",
    grand_total: str | None = None,
    custom_data: dict | None = None,
    status: str = "completed",
) -> dict:
    if custom_data is None:
        custom_data = {"order_id": order_id}
    d: dict = {
        "id": txn_id,
        "status": status,
        "currency_code": currency,
        "custom_data": custom_data,
        "items": [{"price": {"id": price_id}, "quantity": 1}],
    }
    if grand_total is not None:
        d["details"] = {"totals": {"grand_total": grand_total, "currency_code": currency}}
    return {
        "event_id": event_id,
        "event_type": "transaction.completed",
        "occurred_at": "2026-09-16T12:00:00Z",
        "notification_id": f"ntf_{event_id}",
        "data": d,
    }


def _create_order(client) -> dict:
    resp = client.post("/api/orders", json={
        "firstName": "Jean",
        "lastName": "Dupont",
        "email": "jean@example.com",
        "phone": "+212 6 12 34 56 78",
        "company": "ACME SARL",
        "challenge": "Croissance stagnante",
    })
    assert resp.status_code == 201, resp.json()
    return resp.json()
