"""Minimal partner sign-in for one-way account linking: one local demo user.

Ring requires the Account Link URL to authenticate the user with the partner service before
the nonce is matched; the signed-in identity becomes the (masked) `account_identifier`.

    DEMO_USER_EMAIL           the demo user's email (default demo@doorsight.local)
    DEMO_USER_PASSWORD_HASH   scrypt hash, set with: python scripts/set_demo_password.py
    SESSION_SECRET            key for signing the session cookie (the script sets one)

Everything here is standard library: scrypt for the password, HMAC-SHA256 for the cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

logger = logging.getLogger("auth")

COOKIE_NAME = "doorsight_session"
SESSION_SECONDS = 8 * 3600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
DEFAULT_EMAIL = "demo@doorsight.local"
MAX_FAILURES, FAILURE_WINDOW_S = 5, 300

_ephemeral_secret = secrets.token_bytes(32)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --- passwords ----------------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                   dklen=len(_unb64(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


def demo_user_email() -> str:
    return os.getenv("DEMO_USER_EMAIL", DEFAULT_EMAIL).strip().lower()


def sign_in_configured() -> bool:
    return bool(os.getenv("DEMO_USER_PASSWORD_HASH", "").strip())


def check_credentials(email: str, password: str) -> bool:
    """Constant-time-ish check against the single demo user."""
    email_ok = hmac.compare_digest(email.strip().lower().encode(), demo_user_email().encode())
    password_ok = verify_password(password, os.getenv("DEMO_USER_PASSWORD_HASH", "").strip())
    return email_ok and password_ok


# --- session cookie -------------------------------------------------------------

def _secret() -> bytes:
    configured = os.getenv("SESSION_SECRET", "").strip()
    if configured:
        return configured.encode()
    return _ephemeral_secret  # sessions don't survive a restart without SESSION_SECRET


def make_session(email: str, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": email, "exp": int((now or time.time()) + SESSION_SECONDS)}).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(cookie: str | None, now: float | None = None) -> str | None:
    """Return the signed-in email, or None if the cookie is missing, forged or expired."""
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < (now or time.time()):
        return None
    return data.get("u")


# --- throttling -------------------------------------------------------------------

class LoginThrottle:
    """At most MAX_FAILURES failed sign-ins per client in FAILURE_WINDOW_S seconds."""

    def __init__(self, max_failures: int = MAX_FAILURES, window_s: float = FAILURE_WINDOW_S):
        self.max_failures, self.window_s = max_failures, window_s
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < self.window_s]

    def blocked(self, key: str, now: float | None = None) -> bool:
        with self._lock:
            return len(self._recent(key, now or time.time())) >= self.max_failures

    def fail(self, key: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._failures[key] = self._recent(key, now) + [now]

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def mask_email(email: str) -> str:
    """u***r@example.com — the obfuscated account_identifier Ring shows the user."""
    local, _, domain = email.partition("@")
    masked = local[0] + "***" if len(local) <= 2 else local[0] + "***" + local[-1]
    return f"{masked}@{domain}" if domain else masked
