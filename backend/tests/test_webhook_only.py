from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    _create_order,
    _get_order,
    sign,
    _make_completed_payload,
)


@pytest.fixture()
def wb_client(settings):
    from app.webhook_only import app as webhook_only_app

    with TestClient(webhook_only_app) as c:
        yield c


class TestOnlyWebhookExposed:
    def test_root_is_not_exposed(self, wb_client):
        assert wb_client.get("/").status_code == 404

    def test_docs_is_not_exposed(self, wb_client):
        assert wb_client.get("/docs").status_code == 404
        assert wb_client.get("/redoc").status_code == 404

    def test_openapi_is_not_exposed(self, wb_client):
        assert wb_client.get("/openapi.json").status_code == 404

    def test_admin_and_orders_endpoints_not_exposed(self, wb_client):
        assert wb_client.get("/api/status").status_code == 404
        assert wb_client.get("/api/orders").status_code == 404
        assert wb_client.get("/api/access/workbook/ord_x").status_code == 404

    def test_non_post_method_on_webhook_returns_404(self, wb_client):
        assert wb_client.get("/api/webhooks/paddle").status_code == 404
        assert wb_client.put("/api/webhooks/paddle").status_code == 404
        assert wb_client.post("/api/webhooks/paddle/").status_code == 404


class TestWebhookForwarding:
    def test_webhook_without_signature_rejected(self, wb_client, settings):
        body = json.dumps({"event_id": "evt_nosig"}).encode()
        resp = wb_client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_signature"

    def test_webhook_with_invalid_signature_rejected(self, wb_client, settings):
        body = json.dumps({"event_id": "evt_badsig"}).encode()
        resp = wb_client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={
                "Paddle-Signature": "ts=1;h1=deadbeef",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401

    def test_valid_webhook_forwarded_and_processed(
        self, client, wb_client, settings
    ):
        order = _create_order(client)
        payload = _make_completed_payload(order["order_id"])
        body = json.dumps(payload, separators=(",", ":")).encode()
        resp = wb_client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={
                "Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paid"] is True
        stored = _get_order(settings, order["order_id"])
        assert stored["status"] == "paid"
        # No SMTP/file/link configured: delivery stays pending, never "sent".
        assert stored["delivery_status"] == "pending"


class TestRawBodyAndHeadersPreserved:
    def test_raw_body_signature_validates_after_forward(self, wb_client, settings):
        # Signature is computed over the exact raw bytes; if the forward path
        # altered the body or dropped Paddle-Signature, this would 401.
        payload = _make_completed_payload("ord_custom_wb_00001", event_id="evt_raw_1")
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = sign(body, settings.paddle_webhook_secret)
        resp = wb_client.post(
            "/api/webhooks/paddle",
            content=body,
            headers={
                "Paddle-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        # Unknown order → signature validated (200 `rejected`), not 401.
        assert resp.status_code == 200
        assert resp.json().get("rejected") == "unknown-order"