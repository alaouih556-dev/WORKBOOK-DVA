from __future__ import annotations

import logging
import smtplib
import ssl
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
    if not settings.email_host or not settings.email_from:
        missing.append("SMTP not configured (EMAIL_HOST / EMAIL_FROM)")
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
    expiry = _link_expiry_text(ttl_hours)
    first = order["first_name"]
    last = order["last_name"]
    full_name = f"{first} {last}"
    order_number = order["order_number"]

    subject = "Votre accès : Workbook DVA + Masterclass Caltur"

    plaintext = (
        f"Bonjour {full_name},\n\n"
        f"Votre commande {order_number} est confirmée.\n\n"
        f"Voici vos accès (valables jusqu'au {expiry}) :\n\n"
        f"- Workbook DVA : {links['workbook']}\n"
        f"- Masterclass DVA : {links['masterclass']}\n\n"
        f"Merci pour votre confiance.\n— Caltur"
    )

    html = f"""\
<html>
<body style="font-family:Arial,sans-serif;color:#343433;max-width:600px;margin:0 auto;">
  <p>Bonjour <strong>{full_name}</strong>,</p>
  <p>Votre commande <strong>{order_number}</strong> est confirmée.</p>
  <p>Voici vos accès (valables jusqu'au <strong>{expiry}</strong>) :</p>
  <ul style="list-style:none;padding:0;">
    <li style="margin-bottom:12px;">
      <a href="{links['workbook']}"
         style="display:inline-block;padding:10px 24px;background:#80C247;color:#fff;text-decoration:none;font-weight:bold;border-radius:4px;">
        Accéder au Workbook DVA
      </a>
    </li>
    <li>
      <a href="{links['masterclass']}"
         style="display:inline-block;padding:10px 24px;background:#3D6120;color:#fff;text-decoration:none;font-weight:bold;border-radius:4px;">
        Accéder à la Masterclass DVA
      </a>
    </li>
  </ul>
  <p style="margin-top:32px;color:#666;">Merci pour votre confiance.<br/>— Caltur</p>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    msg["To"] = order["email"]
    msg.attach(MIMEText(plaintext, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _send_smtp(
    settings: Settings,
    to: str,
    msg: MIMEMultipart,
) -> tuple[bool, str]:
    try:
        if settings.email_use_ssl:
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(settings.email_host, settings.email_port, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(settings.email_host, settings.email_port, timeout=20)
            if settings.email_use_tls:
                server.starttls()
        try:
            if settings.email_username:
                server.login(settings.email_username, settings.email_password)
            server.sendmail(settings.email_from, [to], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return True, ""
    except Exception as exc:  # pragma: no cover
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
            ok, error = _send_smtp(settings, order["email"], msg)
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
