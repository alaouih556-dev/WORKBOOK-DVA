from __future__ import annotations

from tests.conftest import (
    _create_order,
    _get_order,
    _FAKE_TXN_ID,
    last_create_transaction_call,
)


class TestOrderCreation:
    def test_creates_order_and_returns_checkout(self, client, settings):
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "jean@example.com",
            "phone": "+212 6 12 34 56 78",
            "company": "ACME SARL",
            "challenge": "Croissance stagnante",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["order_id"].startswith("ord_")
        assert data["status"] == "checkout_started"
        assert data["checkout_url"].startswith("https://")
        assert data["checkout_token"]
        assert data["transaction_id"] == _FAKE_TXN_ID

        order = _get_order(settings, data["order_id"])
        assert order["paddle_transaction_id"] == _FAKE_TXN_ID
        assert order["paddle_price_id"] == settings.paddle_price_id
        assert order["email"] == "jean@example.com"

    def test_order_id_sent_in_paddle_custom_data(self, client, settings):
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "jean@example.com",
            "phone": "+212 6 12 34 56 78",
            "company": "ACME SARL",
            "challenge": "Croissance stagnante",
        })
        assert resp.status_code == 201
        data = resp.json()

        txn_call = last_create_transaction_call()
        assert txn_call is not None
        assert txn_call["custom_data"]["order_id"] == data["order_id"]
        assert txn_call["custom_data"]["order_number"] == data["order_number"]
        assert txn_call["custom_data"].get("source") == "workbook-site"

    def test_missing_price_id_returns_503_payment_not_configured(self, client, settings, monkeypatch):
        monkeypatch.setenv("PADDLE_PRICE_ID", "")
        from app.config import get_settings
        get_settings.cache_clear()
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "jean@example.com",
            "phone": "+212 6 12 34 56 78",
            "company": "ACME SARL",
            "challenge": "Autre",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"] == "payment_not_configured"


class TestValidation:
    def test_missing_fields_fails(self, client):
        resp = client.post("/api/orders", json={"firstName": "Jean"})
        assert resp.status_code == 422

    def test_invalid_email_fails(self, client):
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "not-an-email",
            "phone": "+212 6 12 34 56 78",
            "company": "ACME SARL",
            "challenge": "Autre",
        })
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"]

    def test_unknown_challenge_fails(self, client):
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "jean@example.com",
            "phone": "+212 6 12 34 56 78",
            "company": "ACME SARL",
            "challenge": "Je ne sais pas",
        })
        assert resp.status_code == 400
        assert "challenge" in resp.json()["detail"]

    def test_bad_phone_fails(self, client):
        resp = client.post("/api/orders", json={
            "firstName": "Jean",
            "lastName": "Dupont",
            "email": "jean@example.com",
            "phone": "abc",
            "company": "ACME SARL",
            "challenge": "Autre",
        })
        assert resp.status_code == 400
        assert "phone" in resp.json()["detail"]


class TestOrderStatusEndpoint:
    def test_status_with_correct_token(self, client, settings):
        order = _create_order(client)
        resp = client.get(
            f"/api/orders/{order['order_id']}/status",
            params={"token": order["checkout_token"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "checkout_started"
        assert data["paid"] is False
        assert data["delivery_status"] is None

    def test_status_requires_correct_token(self, client):
        order = _create_order(client)
        resp = client.get(
            f"/api/orders/{order['order_id']}/status",
            params={"token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_unknown_order_404(self, client):
        resp = client.get(
            "/api/orders/ord_unknown/status",
            params={"token": "whatever"},
        )
        assert resp.status_code == 404


class TestAdminStatusEndpoint:
    def test_admin_status_requires_token(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 422  # token missing

    def test_admin_status_wrong_token_forbidden(self, client):
        resp = client.get("/api/status", params={"token": "nope"})
        assert resp.status_code == 403

    def test_admin_status_ok(self, client, settings):
        _create_order(client)
        resp = client.get("/api/status", params={"token": settings.admin_status_token})
        assert resp.status_code == 200
        data = resp.json()
        cfg = data["config"]
        assert cfg["paddle_api_key_present"] is True
        assert cfg["email_configured"] is False
        assert cfg["workbook_file_present"] is True
        assert data["stats"]["orders_total"] == 1
        assert "jean" not in str(data["recent_orders"])  # masked, no raw email
        assert "example.com" not in str(data["recent_orders"])

    def test_admin_status_masked_email_is_masked(self, client, settings):
        _create_order(client)
        resp = client.get("/api/status", params={"token": settings.admin_status_token})
        masked = resp.json()["recent_orders"][0]["email_masked"]
        assert "@" in masked
        assert "jean" not in masked


class TestHomePage:
    def test_index_serves_html_readonly(self, client, settings):
        resp = client.get("/")
        # The configured HTML path exists during tests if present locally;
        # otherwise the route returns 404 JSON. Either way we never modify it.
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert "text/html" in resp.headers["content-type"]
            assert "Méthode DVA" in resp.text