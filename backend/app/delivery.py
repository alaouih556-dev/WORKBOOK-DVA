from __future__ import annotations

import logging
import httpx
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .config import Settings
from .database import Database
from .models import get_order, set_delivery
from .security import make_signed_link_token

logger = logging.getLogger(__name__)


def _prerequisites(settings: Settings) -> list[str]:
    missing: list[str] = []
    if not settings.access_link_secret:
        missing.append("ACCESS_LINK_SECRET missing")
    if not settings.public_base_url:
        missing.append("PUBLIC_BASE_URL missing")
    if not settings.resend_api_key:
        missing.append("RESEND_API_KEY missing")
    if not settings.email_from:
        missing.append("EMAIL_FROM missing")
    if not settings.workbook_path or not settings.workbook_path.is_file():
        missing.append("DELIVERY_FILE_WORKBOOK missing or file not found")
    return missing


def build_links(settings: Settings, order_id: str, ttl_hours: int) -> dict[str, str]:
    base = settings.public_base_url.rstrip("/")
    wb_token = make_signed_link_token(
        settings.access_link_secret,
        order_id=order_id,
        bucket="workbook",
        ttl_hours=ttl_hours,
    )
    mc_token = make_signed_link_token(
        settings.access_link_secret,
        order_id=order_id,
        bucket="masterclass",
        ttl_hours=ttl_hours,
    )
    return {
        "workbook": f"{base}/api/access/workbook/{order_id}?token={wb_token}",
        "masterclass": f"{base}/api/access/masterclass/{order_id}?token={mc_token}",
    }


def _link_expiry_text(ttl_hours: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    return dt.strftime("%d/%m/%Y %H:%M UTC")


def _build_email(
    order: dict[str, Any],
    links: dict[str, str],
    settings: Settings,
    ttl_hours: int,
) -> MIMEMultipart:
    from html import escape

    first = str(order.get("first_name") or "").strip()
    first = first or "Client"

    workbook_url = str(links.get("workbook") or "").strip()
    whatsapp_url = str(settings.whatsapp_group_url or "").strip()

    subject = "Votre Workbook DVA est pr\u00eat"

    plaintext = (
        f"Bonjour {first},\n\n"
        "Votre paiement est bien confirm\u00e9 et je vous souhaite la bienvenue dans la M\u00e9thode DVA.\n\n"
        "Voici le lien s\u00e9curis\u00e9 pour t\u00e9l\u00e9charger votre Workbook DVA et entamer votre diagnostic imm\u00e9diatement :\n\n"
        f"T\u00e9l\u00e9charger mon Workbook DVA : {workbook_url}\n\n"
        "Comment acc\u00e9der \u00e0 votre Masterclass offerte ?\n\n"
        "Travailler avec le Workbook DVA n'est que la premi\u00e8re \u00e9tape. "
        "Nous vous transmettrons d'ici une semaine, par email, la date et l\u2019heure de la session en direct.\n\n"
        "Bonus : Communaut\u00e9 M\u00e9thode DVA\n\n"
        "Rejoignez notre groupe priv\u00e9 pour \u00e9changer avec d\u2019autres entrepreneurs engag\u00e9s "
        "dans la m\u00e9thode et b\u00e9n\u00e9ficier de leurs retours d'exp\u00e9rience :\n\n"
        f"Rejoindre le groupe WhatsApp VIP : {whatsapp_url}\n\n"
        "Un dernier conseil :\n\n"
        "Prenez le temps de r\u00e9aliser ce diagnostic en toute sinc\u00e9rit\u00e9. "
        "C'est le premier pas concret pour reprendre le contr\u00f4le de votre activit\u00e9 "
        "et de votre vie d\u2019entrepreneur.\n\n"
        "\u00c0 tr\u00e8s vite,\n\n"
        "Mohammed Amine El Ouariti\n"
        "Directeur G\u00e9n\u00e9ral, Caltur"
    )

    safe_first = escape(first)
    safe_workbook = escape(workbook_url, quote=True)
    safe_whatsapp = escape(whatsapp_url, quote=True)

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#343433;line-height:1.7;max-width:650px;margin:auto;padding:24px;">
        <p>Bonjour {safe_first},</p>

        <p>Votre paiement est bien confirm\u00e9 et je vous souhaite la bienvenue dans la <strong>M\u00e9thode DVA</strong>.</p>

        <p>Voici le lien s\u00e9curis\u00e9 pour t\u00e9l\u00e9charger votre Workbook DVA et entamer votre diagnostic imm\u00e9diatement :</p>

        <p style="margin:28px 0;">
            <a href="{safe_workbook}" style="background:#80C247;color:#ffffff;text-decoration:none;padding:14px 22px;display:inline-block;border-radius:5px;font-weight:bold;">
                T\u00e9l\u00e9charger mon Workbook DVA
            </a>
        </p>

        <h3>Comment acc\u00e9der \u00e0 votre Masterclass offerte ?</h3>

        <p>Travailler avec le Workbook DVA n'est que la premi\u00e8re \u00e9tape.
        Nous vous transmettrons d'ici une semaine, par email, la date et l\u2019heure de la session en direct.</p>

        <h3>Bonus : Communaut\u00e9 M\u00e9thode DVA</h3>

        <p>Rejoignez notre groupe priv\u00e9 pour \u00e9changer avec d\u2019autres entrepreneurs engag\u00e9s
        dans la m\u00e9thode et b\u00e9n\u00e9ficier de leurs retours d'exp\u00e9rience :</p>

        <p style="margin:28px 0;">
            <a href="{safe_whatsapp}" style="background:#25D366;color:#ffffff;text-decoration:none;padding:14px 22px;display:inline-block;border-radius:5px;font-weight:bold;">
                Rejoindre le groupe WhatsApp VIP
            </a>
        </p>

        <p><strong>Un dernier conseil :</strong></p>

        <p>Prenez le temps de r\u00e9aliser ce diagnostic en toute sinc\u00e9rit\u00e9.
        C'est le premier pas concret pour reprendre le contr\u00f4le de votre activit\u00e9
        et de votre vie d\u2019entrepreneur.</p>

        <p>\u00c0 tr\u00e8s vite,</p>

        <p><strong>Mohammed Amine El Ouariti</strong><br>
        Directeur G\u00e9n\u00e9ral, Caltur</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg.attach(MIMEText(plaintext, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    return msg


def _send_resend(
    settings: Settings,
    to: str,
    msg: MIMEMultipart,
) -> tuple[bool, str]:
    try:
        plaintext = ""
        html = ""

        for part in msg.walk():
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue

            raw = part.get_payload(decode=True)
            if raw is None:
                continue

            content = raw.decode(
                part.get_content_charset() or "utf-8",
                errors="replace",
            )

            if part.get_content_type() == "text/plain":
                plaintext = content
            else:
                html = content

        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{settings.email_from_name} <{settings.email_from}>",
                "to": [to],
                "subject": str(msg["Subject"]),
                "text": plaintext,
                "html": html,
            },
            timeout=20.0,
        )

        if 200 <= response.status_code < 300:
            return True, ""

        return False, (
            f"Resend HTTP {response.status_code}: {response.text}"
        )[:300]

    except Exception as exc:
        return False, str(exc)[:300]


def deliver(
    db: Database,
    order_id: str,
    settings: Settings,
) -> dict[str, Any]:
    with db.connection() as conn:
        order = get_order(conn, order_id)

    if not order:
        return {"status": "error", "reason": "order_not_found"}

    if order["status"] != "paid":
        return {"status": "error", "reason": "order_not_paid"}

    missing = _prerequisites(settings)
    with db.connection() as conn:
        order = get_order(conn, order_id)  # re-read for fresh attempts counter
        attempts = order["delivery_attempts"] + 1
        if missing:
            set_delivery(
                conn,
                order_id,
                status="pending",
                attempts=attempts,
                error="; ".join(missing),
            )
            return {"status": "pending", "attempts": attempts, "missing": missing}
        else:
            links = build_links(settings, order_id, settings.access_link_ttl_hours)
            msg = _build_email(order, links, settings, settings.access_link_ttl_hours)
            ok, error = _send_resend(settings, order["email"], msg)
            if ok:
                from .models import utcnow_iso
                set_delivery(
                    conn,
                    order_id,
                    status="sent",
                    attempts=attempts,
                    delivered_at=utcnow_iso(),
                )
            else:
                set_delivery(
                    conn,
                    order_id,
                    status="failed",
                    attempts=attempts,
                    error=error,
                )

    with db.connection() as conn:
        updated = get_order(conn, order_id)

    return {
        "status": updated["delivery_status"],
        "attempts": updated["delivery_attempts"],
    }
