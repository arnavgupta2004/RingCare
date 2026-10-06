"""Configuration loaded from .env. Fails fast with a clear message if anything is missing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_VARS = ("RING_CLIENT_ID", "RING_CLIENT_SECRET", "RING_HMAC_KEY", "RING_ACCESS_TOKEN")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    ring_client_id: str
    ring_client_secret: str
    ring_hmac_key: str
    ring_access_token: str
    data_dir: Path = PROJECT_ROOT / "data"
    logs_dir: Path = PROJECT_ROOT / "logs"

    @property
    def webhook_log_file(self) -> Path:
        return self.logs_dir / "webhooks.jsonl"


def current_access_token() -> str:
    """Sandbox token, re-read from .env on every call so a regenerated 30-minute
    token takes effect without restarting the server."""
    token = (dotenv_values(PROJECT_ROOT / ".env").get("RING_ACCESS_TOKEN") or "").strip()
    return token or get_settings().ring_access_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Real environment variables take precedence over .env.
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f". Copy .env.example to .env in {PROJECT_ROOT} and fill them in."
        )

    return Settings(
        ring_client_id=os.environ["RING_CLIENT_ID"].strip(),
        ring_client_secret=os.environ["RING_CLIENT_SECRET"].strip(),
        ring_hmac_key=os.environ["RING_HMAC_KEY"].strip(),
        ring_access_token=os.environ["RING_ACCESS_TOKEN"].strip(),
    )
