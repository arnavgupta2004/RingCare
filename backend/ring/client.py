"""Async client for the Amazon Vision API (Ring partner API)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("ring.client")

BASE_URL = "https://api.amazonvision.com"
OAUTH_TOKEN_URL = "https://oauth.ring.com/oauth/token"
TIMEOUT_SECONDS = 10.0


class RingAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(f"Ring API error {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class RingTokenExpiredError(RingAPIError):
    def __init__(self, body: Any = None):
        super().__init__(
            401,
            "sandbox token expired, regenerate it in the console "
            "(Ring developer console -> sandbox -> generate OAuth token, then update RING_ACCESS_TOKEN in .env)",
            body,
        )


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


class RingClient:
    """Thin async wrapper with bearer auth, a 10 s timeout and status logging.

    Use as an async context manager:
        async with RingClient(token) as ring:
            devices = await ring.list_devices()
    """

    def __init__(self, access_token: str, base_url: str = BASE_URL, timeout: float = TIMEOUT_SECONDS):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )

    async def __aenter__(self) -> "RingClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        logger.info("-> %s %s", method, path)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            logger.error("x  %s %s timed out after %.0fs", method, path, self._client.timeout.read)
            raise RingAPIError(0, f"timeout calling {method} {path}") from exc
        except httpx.HTTPError as exc:
            logger.error("x  %s %s failed: %s", method, path, exc)
            raise RingAPIError(0, f"network error calling {method} {path}: {exc}") from exc

        logger.info("<- %s %s %s", method, path, response.status_code)
        if response.status_code == 401:
            logger.error("401 from Ring: sandbox token expired, regenerate it in the console")
            raise RingTokenExpiredError(_body(response))
        if response.status_code >= 400:
            raise RingAPIError(response.status_code, response.reason_phrase, _body(response))
        if response.status_code == 204 or not response.content:
            return None
        return _body(response)

    # --- Endpoints -------------------------------------------------------

    async def list_devices(self, include: list[str] | None = None) -> Any:
        """GET /v1/devices, optionally with ?include=status,capabilities,location,configurations."""
        params = {"include": ",".join(include)} if include else None
        return await self.request("GET", "/v1/devices", params=params)

    async def get_user_me(self) -> Any:
        """GET /v1/users/me — returns the Ring Account ID in data.id."""
        return await self.request("GET", "/v1/users/me")


async def exchange_authorization_code(code: str, client_id: str, client_secret: str) -> dict[str, Any]:
    """Exchange a Ring authorization code for access + refresh tokens.

    POST https://oauth.ring.com/oauth/token (application/x-www-form-urlencoded).
    The code is only valid for 60 seconds.
    """
    logger.info("-> POST %s grant_type=authorization_code", OAUTH_TOKEN_URL)
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except httpx.HTTPError as exc:
            logger.error("x  token exchange failed: %s", exc)
            raise RingAPIError(0, f"token exchange network error: {exc}") from exc
    logger.info("<- POST %s %s", OAUTH_TOKEN_URL, response.status_code)
    if response.status_code != 200:
        raise RingAPIError(response.status_code, "token exchange failed", _body(response))
    return response.json()
