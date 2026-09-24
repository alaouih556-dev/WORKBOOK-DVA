from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time


def new_secret_token(nbytes: int = 32) -> str:
    """Unpredictable random string (URL-safe, no padding)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------------------
# Signed, expiring access links
# ------------------------------------------------------------------
# Token structure:  <base64(payload)>.<hmac-hex>
# Payload:  "<order_id>:<bucket>:<expires_unix>"
#


def make_signed_link_token(
    secret: str,
    *,
    order_id: str,
    bucket: str,
    ttl_hours: int,
) -> str:
    expires = int(time.time()) + ttl_hours * 3600
    payload = f"{order_id}:{bucket}:{expires}"
    sig = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{raw}.{sig}"


def verify_signed_link_token(
    secret: str,
    *,
    order_id: str,
    bucket: str,
    token: str,
    now: int | None = None,
) -> bool:
    if now is None:
        now = int(time.time())
    try:
        raw, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return False
    try:
        payload_bytes = base64.urlsafe_b64decode(raw + "=" * ((-len(raw)) % 4))
        payload = payload_bytes.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    parts = payload.split(":")
    if len(parts) != 3:
        return False
    p_order, p_bucket, p_expires_str = parts
    if p_order != order_id or p_bucket != bucket:
        return False
    try:
        expires = int(p_expires_str)
    except ValueError:
        return False
    if now > expires:
        return False
    expected = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)