from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import delivery, models, paddle, webhook
from .config import Settings, get_settings
from .database import Database
from .schemas import OrderCreate, OrderCreated, OrderStatus
from .security import hash_token
from .validation import validate_order_payload

logger = logging.getLogger(__name__)

_cached_db: Database | None = None


def _get_db() -> Database:
    global _cached_db
    s = get_settings()
    if _cached_db is None or _cached_db.db_path != s.database_path:
        _cached_db = Database(s.database_path)
    return _cached_db


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _get_db().init()
        yield

    app = FastAPI(
        title="Caltur Workbook – Backend Paddle",
        lifespan=lifespan,
    )

    s = get_settings()
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Local dev: serve the existing page (read-only, never modified)
    # ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        p = Path(s.html_path)
        if p.is_file():
            return FileResponse(p, media_type="text/html; charset=utf-8")
        return JSONResponse({"message": "set html_path in .env to serve the page locally"}, status_code=404)

    # ------------------------------------------------------------------
    # Create order + open Paddle checkout
    # ------------------------------------------------------------------
    @app.post("/api/orders", response_model=OrderCreated, status_code=201)
    async def create_order(payload: OrderCreate, settings: Settings = Depends(get_settings)):
        errors = validate_order_payload(payload.model_dump())
        if errors:
            raise HTTPException(400, detail=errors)

        db = _get_db()
        with db.connection() as conn:
            order = models.insert_order(conn, payload.model_dump())
        # Raw checkout token is returned to the client exactly once; only its
        # hash is stored in SQLite, so keep it in a local variable before any
        # DB re-read (get_order() only contains the hash).
        checkout_token = order["checkout_token"]

        # Server-side Paddle API call — Price ID always from config
        try:
            txn = await paddle.create_transaction(
                settings,
                custom_data={
                    "order_id": order["id"],
                    "order_number": order["order_number"],
                    "source": "workbook-site",
                },
            )
        except paddle.PaymentNotConfigured:
            raise HTTPException(
                503, detail="payment_not_configured"
            )
        except paddle.PaddleApiError:
            raise HTTPException(
                502, detail="payment_provider_failed"
            )

        with db.connection() as conn:
            models.set_order_checkout(
                conn,
                order["id"],
                transaction_id=txn["transaction_id"],
                price_id=settings.paddle_price_id,
                currency=settings.paddle_expected_currency,
            )
            order = models.get_order(conn, order["id"])

        return OrderCreated(
            order_id=order["id"],
            order_number=order["order_number"],
            status=order["status"],
            checkout_url=txn["checkout_url"],
            checkout_token=checkout_token,
            transaction_id=txn["transaction_id"],
        )

    # ------------------------------------------------------------------
    # Public Paddle configuration (non-secret) for the frontend checkout
    # ------------------------------------------------------------------
    @app.get("/api/paddle-config")
    def paddle_config(settings: Settings = Depends(get_settings)):
        if not settings.paddle_client_token:
            raise HTTPException(503, detail="payment_not_configured")
        return {
            "environment": settings.paddle_env.lower(),
            "clientToken": settings.paddle_client_token,
        }

    # ------------------------------------------------------------------
    # Order status (protected by checkout_token — returned at creation)
    # ------------------------------------------------------------------
    @app.get(
        "/api/orders/{order_id}/status",
        response_model=OrderStatus,
    )
    def order_status(
        order_id: str,
        token: str = Query(...),
        settings: Settings = Depends(get_settings),
    ):
        db = _get_db()
        with db.connection() as conn:
            order = models.get_order(conn, order_id)

        if not order:
            raise HTTPException(404, detail="order_not_found")

        if not hash_token(token) == order["checkout_token_hash"]:
            raise HTTPException(403, detail="forbidden")

        return OrderStatus(
            order_id=order["id"],
            order_number=order["order_number"],
            status=order["status"],
            paid=order["status"] in ("paid", "delivered"),
            delivery_status=order["delivery_status"],
        )

    # ------------------------------------------------------------------
    # Paddle webhook receiver
    # ------------------------------------------------------------------
    @app.post("/api/webhooks/paddle")
    async def paddle_webhook(request: Request, settings: Settings = Depends(get_settings)):
        raw = await request.body()
        sig = request.headers.get("paddle-signature", "")
        status, body = await webhook.webhook_handler(raw, sig, settings, _get_db())
        return JSONResponse(content=body, status_code=status)

    # ------------------------------------------------------------------
    # Protected download links (expiring, HMAC-signed)
    # ------------------------------------------------------------------
    @app.get("/api/access/workbook/{order_id}")
    def download_workbook(
        order_id: str,
        token: str = Query(...),
        settings: Settings = Depends(get_settings),
    ):
        if not settings.access_link_secret:
            raise HTTPException(503, detail="access_links_not_configured")

        if not security_verify("workbook", order_id, token, settings):
            raise HTTPException(403, detail="invalid_or_expired_link")

        db = _get_db()
        with db.connection() as conn:
            order = models.get_order(conn, order_id)

        if not order or order["status"] != "paid":
            raise HTTPException(403, detail="access_denied")

        path = settings.workbook_path
        if not path or not path.is_file():
            raise HTTPException(503, detail="file_not_ready")

        filename = f"Workbook-DVA-{order['order_number']}.pdf"
        return FileResponse(path, media_type="application/pdf", filename=filename)

    @app.get("/api/access/masterclass/{order_id}")
    def masterclass_access(
        order_id: str,
        token: str = Query(...),
        settings: Settings = Depends(get_settings),
    ):
        if not settings.access_link_secret:
            raise HTTPException(503, detail="access_links_not_configured")

        if not security_verify("masterclass", order_id, token, settings):
            raise HTTPException(403, detail="invalid_or_expired_link")

        db = _get_db()
        with db.connection() as conn:
            order = models.get_order(conn, order_id)

        if not order or order["status"] != "paid":
            raise HTTPException(403, detail="access_denied")

        if not settings.masterclass_url:
            raise HTTPException(503, detail="link_not_ready")

        return RedirectResponse(settings.masterclass_url, status_code=302)

    # ------------------------------------------------------------------
    # Admin status (protected by unpredictable token)
    # ------------------------------------------------------------------
    @app.get("/api/status")
    def admin_status(
        token: str = Query(...),
        settings: Settings = Depends(get_settings),
    ):
        if not settings.admin_status_token:
            raise HTTPException(503, detail="admin_status_not_configured")
        if token != settings.admin_status_token:
            raise HTTPException(403, detail="forbidden")

        db = _get_db()
        with db.connection() as conn:
            stats = models.order_stats(conn)
            recent = models.recent_orders(conn, limit=15)

        from .delivery import _prerequisites

        missing = _prerequisites(settings)

        def mask(e: str) -> str:
            if "@" not in e:
                return "***"
            local, domain = e.split("@", 1)
            domain_parts = domain.split(".")
            dl = domain_parts[0] if domain_parts else ""
            return f"{local[0]}{'*' * max(len(local) - 1, 0)}@{dl[0]}{'*' * 3}.{domain_parts[-1]}" if domain else f"{local[0]}***"

        return {
            "config": {
                "paddle_environment": settings.paddle_env,
                "paddle_api_key_present": bool(settings.paddle_api_key),
                "paddle_price_id_present": bool(settings.paddle_price_id),
                "expected_currency": settings.paddle_expected_currency,
                "webhook_secret_present": bool(settings.paddle_webhook_secret),
                "access_links_configured": bool(settings.access_link_secret),
                "email_configured": bool(settings.email_host and settings.email_from),
                "workbook_file_present": bool(settings.workbook_path and settings.workbook_path.is_file()),
                "masterclass_url_present": bool(settings.masterclass_url),
                "public_base_url": settings.public_base_url,
                "delivery_prerequisites_ok": len(missing) == 0,
                "delivery_missing": missing,
            },
            "stats": stats,
            "recent_orders": [
                {
                    "order_number": r["order_number"],
                    "status": r["status"],
                    "delivery_status": r["delivery_status"],
                    "paid_at": r["paid_at"],
                    "created_at": r["created_at"],
                    "email_masked": mask(r["email"]),
                }
                for r in recent
            ],
        }

    # ------------------------------------------------------------------
    # Admin: manual retry of pending deliveries
    # ------------------------------------------------------------------
    @app.post("/api/admin/deliveries/{order_id}/retry")
    def retry_delivery(
        order_id: str,
        token: str = Query(...),
        settings: Settings = Depends(get_settings),
    ):
        if not settings.admin_status_token or token != settings.admin_status_token:
            raise HTTPException(403, detail="forbidden")
        db = _get_db()
        result = delivery.deliver(db, order_id, settings)
        return JSONResponse(content={"order_id": order_id, **result})

    return app


def security_verify(bucket: str, order_id: str, token: str, settings: Settings) -> bool:
    from .security import verify_signed_link_token
    return verify_signed_link_token(
        settings.access_link_secret,
        order_id=order_id,
        bucket=bucket,
        token=token,
    )


app = create_app()