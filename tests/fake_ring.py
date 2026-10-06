"""An in-memory stand-in for Ring's OAuth and API servers, served through httpx.MockTransport."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

from backend.ring.signatures import compute_nonce

ACCOUNT = "ava1.ring.account.TESTACCOUNT123456"


class FakeRing:
    def __init__(self, hmac_key: str, client_id: str = "test-client", client_secret: str = "test-secret"):
        self.hmac_key, self.client_id, self.client_secret = hmac_key, client_id, client_secret
        self.codes = {"good-code"}
        self.access: set[str] = set()
        self.refresh: set[str] = set()
        self.calls: list[tuple[str, str, dict]] = []
        self.link_time_ms: str | None = None  # the "time" Ring used when it generated the nonce
        self.integration_status: str | None = None
        self.fail_patch = False
        self._n = 0

    # what Ring would put in the /link redirect
    def make_link(self, time_ms: int) -> tuple[str, str]:
        self.link_time_ms = str(time_ms)
        return compute_nonce(self.hmac_key, self.link_time_ms, ACCOUNT), self.link_time_ms

    def _issue(self) -> dict:
        self._n += 1
        at, rt = f"at-{self._n}", f"rt-{self._n}"
        self.access.add(at)
        self.refresh.add(rt)
        return {"access_token": at, "refresh_token": rt, "expires_in": 14400, "scope": "ava.v1:read",
                "token_type": "Bearer"}

    def revoke_access(self, token: str) -> None:
        self.access.discard(token)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode() if request.content else ""
        if request.url.host == "oauth.ring.com":
            form = {k: v[0] for k, v in parse_qs(body).items()}
            self.calls.append(("POST", "oauth/token", {k: v for k, v in form.items() if k == "grant_type"}))
            if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
                return httpx.Response(401, json={"error": "invalid_client"})
            if form["grant_type"] == "authorization_code" and form.get("code") in self.codes:
                self.codes.discard(form["code"])  # one-time use
                return httpx.Response(200, json=self._issue())
            if form["grant_type"] == "refresh_token" and form.get("refresh_token") in self.refresh:
                self.refresh.discard(form["refresh_token"])  # rotation
                return httpx.Response(200, json=self._issue())
            return httpx.Response(400, json={"error": "invalid_grant"})

        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        data = json.loads(body) if body else {}
        self.calls.append((request.method, request.url.path, data))
        if token not in self.access:
            return httpx.Response(401, json={"errors": [{"status": "401", "title": "Invalid Client"}]})
        path = request.url.path
        if path == "/v1/users/me":
            return httpx.Response(200, json={"data": {"type": "users", "id": ACCOUNT,
                                                      "attributes": {"email": "ring-user@example.com"}}})
        if path == "/v1/devices":
            return httpx.Response(200, json={"data": [{"type": "devices", "id": "dev-1",
                                                       "attributes": {"name": "Front Door"}}]})
        if path == "/v1/accounts/me/app-integrations" and request.method == "POST":
            expected = compute_nonce(self.hmac_key, self.link_time_ms or "", ACCOUNT)
            if data.get("nonce") != expected:
                return httpx.Response(400, json={"errors": [{"status": "400", "title": "Invalid Nonce"}]})
            self.integration_status = "awaiting"
            return httpx.Response(200, json={"status": "awaiting"})
        if path == "/v1/accounts/me/app-integrations" and request.method == "PATCH":
            if self.fail_patch:
                return httpx.Response(500, json={"errors": [{"status": "500"}]})
            self.integration_status = data["status"]
            return httpx.Response(200, json={"status": data["status"], "updated_at": "2026-10-06T00:00:00Z"})
        return httpx.Response(404, json={"errors": [{"status": "404"}]})
