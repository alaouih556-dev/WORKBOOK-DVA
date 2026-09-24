from __future__ import annotations

import json
import time

from tests.conftest import (
    sign,
    _create_order,
    _get_event,
    _get_order,
    _count_events,
    _make_completed_payload,
)


class TestSignatureRejection:
    def test_missing_signature_header_returns_400(self, client, settings):
        body = json.dumps({"event_id": "evt1"}).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_signature"

    def test_invalid_signature_returns_401(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={
                "Paddle-Signature": f"ts={int(time.time())};h1={'a' * 64}",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_signature"
        assert _count_events(settings) == 0

    def test_wrong_secret_returns_401(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = sign(body, "wrong_secret_key_00000000000000")
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401

    def test_tampered_body_returns_401(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = sign(body, settings.paddle_webhook_secret)
        tampered = body.replace(
            b"txn_test_paddle_abc123def456",
            b"txn_TEST_TAMPERED_0000000000000",
        )
        assert tampered != body  # make sure we actually changed bytes
        resp = client.post(
            "/api/webhooks/paddle",
            content=tampered,
            headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401

    def test_replay_old_timestamp_rejected(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = sign(body, settings.paddle_webhook_secret, ts=int(time.time()) - 600)
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401


class TestInconsistentTransaction:
    def test_price_mismatch_not_paid(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(
            order["order_id"], price_id="pri_wrong_price_id_00000000000000000x"
        )
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json().get("rejected") == "price-mismatch"
        assert _get_order(settings, order["order_id"])["status"] != "paid"

    def test_currency_mismatch_not_paid(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"], currency="MAD")
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json().get("rejected") == "currency-mismatch"
        assert _get_order(settings, order["order_id"])["status"] != "paid"

    def test_unknown_order_rejected_and_recorded(self, client, settings):
        payload = _make_completed_payload("ord_nonexistent_1234567890", event_id="evt_unknown")
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json().get("rejected") == "unknown-order"
        assert _count_events(settings) == 1
        assert _get_event(settings, "evt_unknown")["outcome"] == "rejected:unknown-order"

    def test_transaction_id_mismatch_rejected(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"], txn_id="txn_different_one_0000000000")
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json().get("rejected") == "transaction-mismatch"
        assert _get_order(settings, order["order_id"])["status"] != "paid"


class TestHappyPath:
    def test_valid_webhook_marks_paid_and_delivery_pending(self, client, settings):
        order = _create_order(client)
        oid = order["order_id"]
        payload = _make_completed_payload(oid)
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paid"] is True
        assert data["delivery"]["status"] == "pending"  # SMTP not configured

        order_after = _get_order(settings, oid)
        assert order_after["status"] == "paid"
        assert order_after["paid_at"] is not None
        assert _count_events(settings) == 1
        assert _get_event(settings, "evt_test_001")["outcome"] == "order-paid"


class TestIdempotency:
    def test_duplicate_event_not_reprocessed(self, client, settings):
        order = _create_order(client)
        oid = order["order_id"]
        payload = _make_completed_payload(oid, event_id="evt_duplicate_1")
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                   "Content-Type": "application/json"}

        resp1 = client.post("/api/webhooks/paddle", content=body, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["paid"] is True
        assert _get_order(settings, oid)["delivery_attempts"] == 1

        resp2 = client.post("/api/webhooks/paddle", content=body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json().get("duplicate") is True

        assert _count_events(settings) == 1
        assert _get_order(settings, oid)["delivery_attempts"] == 1

    def test_two_events_same_order_both_recorded(self, client, settings):
        order = _create_order(client)
        payloads = [
            _make_completed_payload(order["order_id"], event_id="evt_aaa", grand_total="1000"),
            _make_completed_payload(order["order_id"], event_id="evt_bbb", grand_total="1000"),
        ]
        for payload in payloads:
            body = json.dumps(payload, separators=(",", ":")).encode()
            resp = client.post(
                "/api/webhooks/paddle",
                content=body,
                headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                         "Content-Type": "application/json"},
            )
            assert resp.status_code == 200
        assert _count_events(settings) == 2


class TestNonCompletedEventIgnored:
    def test_transaction_created_is_ignored(self, client, settings):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        payload["event_type"] = "transaction.created"
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                   "Content-Type": "application/json"}
        resp = client.post("/api/webhooks/paddle", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json().get("ignored") is True
        assert _get_order(settings, order["order_id"])["status"] == "checkout_started"
        assert _count_events(settings) == 1