from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class PaymentNotConfigured(RuntimeError):
    """Raised when the API key or price ID is missing from configuration."""


class PaddleApiError(RuntimeError):
    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"paddle_api_error (status={status})")
        self.status = status
        self.body = body


async def create_transaction(
    settings: Settings,
    *,
    custom_data: dict[str, Any],
    quantity: int = 1,
) -> dict[str, Any]:
    """Create a Sandbox Paddle transaction server-side.

    The Price ID always comes from configuration, never from the browser.
    We do not pass any customer/address so Paddle returns the checkout URL
    (draft transaction) for the customer to complete.

    Important: if ``PADDLE_CHECKOUT_URL`` is empty we let Paddle use the
    account's default payment link. The default link must NOT be modified in
    the Paddle dashboard (it points to another project); creating an
    additional approved link for this site does not change the default.
    """
    if not settings.paddle_api_key or not settings.paddle_price_id:
        raise PaymentNotConfigured(
            "PADDLE_API_KEY or PADDLE_PRICE_ID missing"
        )

    body: dict[str, Any] = {
        "items": [{"quantity": quantity, "price_id": settings.paddle_price_id}],
        "custom_data": custom_data,
        "collection_mode": "automatic",
    }
    if settings.paddle_checkout_url:
        body["checkout"] = {"url": settings.paddle_checkout_url}

    headers = {"Authorization": f"Bearer {settings.paddle_api_key}"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{settings.paddle_api_base_url}/transactions",
            json=body,
            headers=headers,
        )

    if resp.status_code >= 400:
        # Log status only: the body may contain Paddle request IDs, never the key.
        logger.warning(
            "Paddle create_transaction failed status=%s", resp.status_code
        )
        raise PaddleApiError(resp.status_code)

    data = resp.json().get("data") or {}
    txn_id = data.get("id")
    checkout_url = (data.get("checkout") or {}).get("url")
    if not txn_id or not checkout_url:
        raise PaddleApiError(202, "no checkout url returned")
    return {
        "transaction_id": txn_id,
        "checkout_url": checkout_url,
        "status": data.get("status"),
    }