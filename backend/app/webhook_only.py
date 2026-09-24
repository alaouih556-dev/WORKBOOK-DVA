from __future__ import annotations

import logging
from typing import Any

from starlette.responses import JSONResponse

from .main import _get_db, app as main_app

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/webhooks/paddle"
ALLOWED_METHOD = "POST"

_NOT_FOUND = JSONResponse({"detail": "not_found"}, status_code=404)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Webhook-only ASGI entry.

    - Accepts exactly ``POST /api/webhooks/paddle``.
    - Forwards the request untouched to the existing ``app.main.app``
      (same scope, headers — including ``Paddle-Signature`` — and the raw
      body stream are passed through as-is).
    - Returns 404 for every other path and method. No HTML, no /docs, no
      OpenAPI, no admin endpoints, no files are reachable here.
    """
    if scope["type"] == "lifespan":
        await _run_lifespan(receive, send)
        return

    if scope.get("type") != "http":
        return

    if scope.get("path") != WEBHOOK_PATH or scope.get("method") != ALLOWED_METHOD:
        await _NOT_FOUND(scope, receive, send)
        return

    # Signature verification and processing are fully re-used inside main_app.
    await main_app(scope, receive, send)


async def _run_lifespan(receive: Any, send: Any) -> None:
    # The existing FastAPI app is called directly as ASGI here, so its own
    # lifespan never runs; initialise the same database schema it would.
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                _get_db().init()
            except Exception as exc:
                logger.exception("startup failed")
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return