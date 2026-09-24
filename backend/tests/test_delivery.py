from __future__ import annotations

import json

from app.delivery import build_links
from app.security import make_signed_link_token
from tests.conftest import _create_order, _get_order, sign, _make_completed_payload


def _pay_order(client, settings, order_id: str, event_id: str = "evt_delivery_1"):
    payload = _make_completed_payload(order_id, event_id=event_id)
    body = json.dumps(payload, separators=(",", ":")).encode()
    resp = client.post(
        "/api/webhooks/paddle",
        content=body,
        headers={"Paddle-Signature": sign(body, settings.paddle_webhook_secret),
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json().get("paid") is True
    return _get_order(settings, order_id)


class TestAccessLinks:
    def test_workbook_download_after_payment(self, client, settings):
        order = _create_order(client)
        paid = _pay_order(client, settings, order["order_id"])
        links = build_links(settings, paid["id"], settings.access_link_ttl_hours)

        resp = client.get(links["workbook"])
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 fake workbook content"
        assert "Workbook-DVA-" in resp.headers["content-disposition"]

    def test_masterclass_redirect_after_payment(self, client, settings):
        order = _create_order(client)
        paid = _pay_order(client, settings, order["order_id"])
        links = build_links(settings, paid["id"], settings.access_link_ttl_hours)

        resp = client.get(links["masterclass"], follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://zoom.us/")

    def test_unpaid_order_cannot_download(self, client, settings):
        order = _create_order(client)  # not paid
        links = build_links(settings, order["order_id"], settings.access_link_ttl_hours)
        resp = client.get(links["workbook"])
        assert resp.status_code == 403

    def test_tampered_token_rejected(self, client, settings):
        order = _create_order(client)
        _pay_order(client, settings, order["order_id"])
        good = make_signed_link_token(
            settings.access_link_secret, order_id=order["order_id"],
            bucket="workbook", ttl_hours=settings.access_link_ttl_hours,
        )
        tampered = good[:-4] + ("abcd" if not good.endswith("abcd") else "dcba")
        resp = client.get(
            f"/api/access/workbook/{order['order_id']}",
            params={"token": tampered},
        )
        assert resp.status_code == 403

    def test_expired_token_rejected(self, client, settings):
        order = _create_order(client)
        _pay_order(client, settings, order["order_id"])
        expired = make_signed_link_token(
            settings.access_link_secret, order_id=order["order_id"],
            bucket="workbook", ttl_hours=-1,
        )
        resp = client.get(
            f"/api/access/workbook/{order['order_id']}",
            params={"token": expired},
        )
        assert resp.status_code == 403


class TestDeliveryPrerequisites:
    def test_pending_delivery_reports_missing_smtp(self, client, settings):
        order = _create_order(client)
        paid = _pay_order(client, settings, order["order_id"])
        assert paid["delivery_status"] == "pending"
        assert "SMTP" in (paid["delivery_error"] or "")

    def test_retry_when_email_configured_sends(self, client, settings, monkeypatch):
        from app.delivery import _send_smtp

        order = _create_order(client)
        _pay_order(client, settings, order["order_id"])

        monkeypatch.setattr("app.delivery._send_smtp", lambda *a, **k: (True, ""))

        # Re-run delivery through the admin retry endpoint once SMTP is "configured"
        monkeypatch.setattr(
            "app.delivery._prerequisites",
            lambda s: [],
        )
        resp = client.post(
            f"/api/admin/deliveries/{order['order_id']}/retry",
            params={"token": settings.admin_status_token},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"
        assert _get_order(settings, order["order_id"])["delivery_status"] == "sent"

    def test_retry_requires_admin_token(self, client, settings):
        order = _create_order(client)
        _pay_order(client, settings, order["order_id"])
        resp = client.post(
            f"/api/admin/deliveries/{order['order_id']}/retry",
            params={"token": "wrong"},
        )
        assert resp.status_code == 403

    def test_file_missing_blocks_delivery(self, client, settings, monkeypatch):
        order = _create_order(client)
        _pay_order(client, settings, order["order_id"])

        original_path = settings.delivery_file_workbook
        settings.delivery_file_workbook = "C:/nonexistent/workbook.pdf"
        monkeypatch.setattr("app.delivery._send_smtp", lambda *a, **k: (True, ""))
        try:
            resp = client.post(
                f"/api/admin/deliveries/{order['order_id']}/retry",
                params={"token": settings.admin_status_token},
            )
        finally:
            settings.delivery_file_workbook = original_path

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert any("DELIVERY_FILE_WORKBOOK" in m for m in resp.json()["missing"])