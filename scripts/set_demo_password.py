"""Set the DoorSight demo user's email and password for the /link sign-in.

Writes DEMO_USER_EMAIL and DEMO_USER_PASSWORD_HASH (scrypt) to .env, plus a random
SESSION_SECRET if none exists. The password itself is never stored or printed.

    python scripts/set_demo_password.py
The running server picks it up on the next request (no restart needed).
"""

from __future__ import annotations

import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import DEFAULT_EMAIL, hash_password  # noqa: E402

ENV = Path(__file__).resolve().parent.parent / ".env"
MIN_LENGTH = 10


def set_env(values: dict[str, str]) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    lines = [l for l in lines if not any(l.startswith(f"{k}=") for k in values)]
    lines += [f"{k}={v}" for k, v in values.items()]
    ENV.write_text("\n".join(lines) + "\n")


def main() -> int:
    email = input(f"Demo user email [{DEFAULT_EMAIL}]: ").strip().lower() or DEFAULT_EMAIL
    password = getpass.getpass(f"Password (at least {MIN_LENGTH} characters): ")
    if len(password) < MIN_LENGTH:
        print("Too short; nothing changed.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords don't match; nothing changed.", file=sys.stderr)
        return 1
    values = {"DEMO_USER_EMAIL": email, "DEMO_USER_PASSWORD_HASH": hash_password(password)}
    existing = ENV.read_text() if ENV.exists() else ""
    if "\nSESSION_SECRET=" not in "\n" + existing:
        values["SESSION_SECRET"] = secrets.token_urlsafe(32)
    set_env(values)
    print(f"Saved sign-in for {email} to .env (password stored only as a scrypt hash). No restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
