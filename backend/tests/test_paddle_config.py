from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestPaddleConfigEndpoint:
    def test_returns_only_environment_and_client_token(self, client, settings):
        resp = client.get("/api/paddle-config")
        assert resp.status_code == 200
        data = resp.json()
        # Only the two public fields may ever be returned.
        assert set(data.keys()) == {"environment", "clientToken"}
        assert data["environment"] == settings.paddle_env.lower()
        assert data["clientToken"] == settings.paddle_client_token

    def test_never_leaks_secrets(self, client, settings):
        resp = client.get("/api/paddle-config")
        assert resp.status_code == 200
        body = resp.text
        assert "pdl_sandbox_test_key_123" not in body  # API key
        assert "pdl_ntfset" not in body  # webhook secret
        assert "api_key" not in body.lower()
        assert "webhook_secret" not in body.lower()
        assert ".env" not in body
        assert "PADDLE_" not in body

    def test_missing_client_token_returns_503(self, client, settings, monkeypatch):
        monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "")
        from app.config import get_settings

        get_settings.cache_clear()
        resp = client.get("/api/paddle-config")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "payment_not_configured"


class TestPaddleConfigNotOnWebhookServer:
    def test_paddle_config_is_not_exposed_on_webhook_only_app(self, settings):
        from app.webhook_only import app as webhook_only_app

        with TestClient(webhook_only_app) as c:
            # Port 8001 (webhook-only) must not expose /api/paddle-config.
            assert c.get("/api/paddle-config").status_code == 404
            assert c.post("/api/paddle-config").status_code == 404
            assert c.get("/api/paddle-config/").status_code == 404


class TestPaddleConfigFrontendMarker:
    def test_order_creation_never_sends_email_or_content_to_paddle(
        self, client, settings
    ):
        # The checkout flow already stores the order with Paddle custom_data
        # limited to order_id/order_number/source (see test_api).
        from tests.conftest import last_create_transaction_call, _create_order

        order = _create_order(client)
        txn_call = last_create_transaction_call()
        assert txn_call is not None
        custom = txn_call["custom_data"]
        assert "email" not in custom
        assert "firstName" not in custom
        assert "lastName" not in custom
        assert "phone" not in custom
        assert "company" not in custom
        assert "challenge" not in custom
        assert custom["order_id"] == order["order_id"]