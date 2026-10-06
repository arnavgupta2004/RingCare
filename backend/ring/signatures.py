"""HMAC helpers for Ring webhooks (hex) and account-linking nonces (URL-safe Base64).

Both use the same HMAC signing key, used as UTF-8 bytes exactly as issued
(do NOT Base64-decode it), but with different output encodings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

SIGNATURE_HEADER = "X-Signature"
SIGNATURE_PREFIX = "sha256="
NONCE_WINDOW_SECONDS = 600


def compute_webhook_signature(signing_key: str, raw_body: bytes) -> str:
    digest = hmac.new(signing_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_webhook_signature(signing_key: str, raw_body: bytes, received: str | None) -> bool:
    """Verify `X-Signature: sha256=<hex>` against the raw request body bytes."""
    if not received:
        return False
    expected = compute_webhook_signature(signing_key, raw_body)
    return hmac.compare_digest(expected.encode("utf-8"), received.strip().encode("utf-8"))


def compute_nonce(signing_key: str, time_ms: str, account_id: str) -> str:
    """nonce = Base64URL_NoPadding(HMAC-SHA256(key, "<time_ms>:<account_id>"))."""
    payload = f"{time_ms}:{account_id}".encode("utf-8")
    mac = hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def nonce_matches(signing_key: str, nonce: str, time_ms: str, account_id: str) -> bool:
    expected = compute_nonce(signing_key, time_ms, account_id)
    return hmac.compare_digest(expected.encode("ascii"), nonce.encode("ascii", "replace"))


def check_link_time(time_ms: str, now_ms: int) -> str | None:
    """Return an error string if the `time` param is invalid or outside the 600 s window."""
    try:
        sent_ms = int(time_ms)
    except (TypeError, ValueError):
        return "time parameter is not a millisecond timestamp"
    delta_s = (now_ms - sent_ms) / 1000
    if delta_s > NONCE_WINDOW_SECONDS:
        return "link request expired (older than 10 minutes)"
    if delta_s < 0:
        return "invalid timestamp: cannot be in the future"
    return None
