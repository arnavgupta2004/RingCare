"""Local JSON token store (data/tokens.json, gitignored).

Keyed by Ring Account ID. Records start as "unclaimed" and become "claimed"
once account linking matches the nonce.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def all(self) -> dict[str, Any]:
        with _lock:
            return self._read()

    def save_unclaimed(self, account_id: str, tokens: dict[str, Any]) -> None:
        now = time.time()
        with _lock:
            data = self._read()
            data[account_id] = {
                "account_id": account_id,
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "scope": tokens.get("scope"),
                "token_type": tokens.get("token_type"),
                "expires_at": now + float(tokens.get("expires_in", 0)),
                "created_at": now,
                "status": "unclaimed",
            }
            self._write(data)

    def mark_claimed(self, account_id: str) -> None:
        with _lock:
            data = self._read()
            if account_id in data:
                data[account_id]["status"] = "claimed"
                data[account_id]["claimed_at"] = time.time()
                self._write(data)
