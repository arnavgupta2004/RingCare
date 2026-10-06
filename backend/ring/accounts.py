"""Linked Ring accounts: one-way account linking, token refresh and token selection.

Flow (Ring one-way account linking):
  1. Ring POSTs an auth code to /token  -> receive_code(): exchange it (60 s window), GET /v1/users/me
     for the Ring account ID, store the tokens as an *unclaimed* account.
  2. Ring redirects the user to /link?nonce&time. The user signs in to DoorSight (mandatory), then
     complete_link(): check freshness (600 s), match the nonce against unclaimed accounts
     (HMAC-SHA256 over "<time>:<account_id>", URL-safe Base64, constant-time compare),
     POST /v1/accounts/me/app-integrations {nonce, account_identifier}   -> status awaiting
     PATCH /v1/accounts/me/app-integrations {status: completed}           -> required to finish
  3. API calls use the linked account's token (refreshed 5 min before expiry, and once on a 401),
     falling back to RING_ACCESS_TOKEN (sandbox) when no account is linked.

Token values never reach logs: logs name the source as "linked:…<last 6 of account id>" or "sandbox".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from backend.auth import mask_email
from backend.ring.client import (
    RingAPIError,
    RingClient,
    RingTokenExpiredError,
    exchange_authorization_code,
    refresh_access_token,
)
from backend.ring.signatures import check_link_time, nonce_matches
from backend.store import LinkedAccount, StateStore

logger = logging.getLogger("ring.accounts")

REFRESH_MARGIN_S = 300  # refresh when the access token has less than 5 minutes left
SCHEDULED_REFRESH_S = 24 * 3600  # refresh at least daily so the ~30-day refresh token never lapses
UNCLAIMED_TTL_S = 15 * 60  # unclaimed tokens are only matchable for 15 minutes


class LinkError(RuntimeError):
    """Account linking failed; the message is safe to show the user."""


class TokenUnavailable(RuntimeError):
    pass


def token_label(account_id: str) -> str:
    return f"linked:…{account_id[-6:]}"


class AccountService:
    def __init__(self, store: StateStore, *, client_id: str, client_secret: str, hmac_key: str,
                 sandbox_token: Callable[[], str | None], transport: httpx.AsyncBaseTransport | None = None,
                 now: Callable[[], float] = time.time):
        self.store = store
        self.client_id, self.client_secret, self.hmac_key = client_id, client_secret, hmac_key
        self.sandbox_token = sandbox_token
        self.transport = transport
        self.now = now
        self._locks: dict[str, asyncio.Lock] = {}

    # --- step 1: /token -------------------------------------------------------------

    async def receive_code(self, code: str) -> LinkedAccount:
        tokens = await exchange_authorization_code(code, self.client_id, self.client_secret, self.transport)
        async with RingClient(tokens["access_token"], source="linked:new", transport=self.transport) as ring:
            me = await ring.get_user_me()
        try:
            account_id = me["data"]["id"]
        except (KeyError, TypeError) as exc:
            raise RingAPIError(200, "GET /v1/users/me response has no data.id") from exc
        now = self.now()
        previous = self.store.get_account(account_id)
        account = LinkedAccount(
            account_id=account_id, status="unclaimed", access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"), expires_at=now + float(tokens["expires_in"]),
            created_at=now, scope=tokens.get("scope"), token_type=tokens.get("token_type"),
            partner_user=previous.partner_user if previous else None,
        )
        self.store.save_account(account)
        logger.info("/token: stored unclaimed tokens for Ring account %s (expires in %ss, scope %s)",
                    token_label(account_id), int(float(tokens["expires_in"])), tokens.get("scope"))
        return account

    # --- step 2: /link ----------------------------------------------------------------

    def match_nonce(self, nonce: str, time_ms: str) -> LinkedAccount | None:
        now = self.now()
        for acc in self.store.list_accounts():
            if acc.status != "unclaimed" or not acc.access_token or now - acc.created_at > UNCLAIMED_TTL_S:
                continue
            if nonce_matches(self.hmac_key, nonce, time_ms, acc.account_id):
                return acc
        return None

    async def complete_link(self, nonce: str, time_ms: str, partner_email: str) -> LinkedAccount:
        """Only call after the partner user has signed in."""
        err = check_link_time(time_ms, int(self.now() * 1000))
        if err:
            raise LinkError(f"This link request has expired or is invalid ({err}). Please start again from the Ring app.")
        account = self.match_nonce(nonce, time_ms)
        if account is None:
            raise LinkError("We couldn't match this request to a Ring authorization. "
                            "Please start again from the Ring app.")
        identifier = mask_email(partner_email)
        try:
            confirm = await self.call(lambda ring: ring.confirm_account_link(nonce, identifier), account)
        except (RingAPIError, TokenUnavailable) as exc:
            logger.warning("link: Ring rejected the nonce for %s: %s", token_label(account.account_id), exc)
            raise LinkError("Ring could not confirm this link request. Please start again from the Ring app.") from exc
        logger.info("link: nonce confirmed for %s (status %s)", token_label(account.account_id),
                    (confirm or {}).get("status"))

        account = self.store.get_account(account.account_id)  # tokens may have been refreshed
        account.status, account.linked_at = "linked", self.now()
        account.partner_user, account.account_identifier = partner_email, identifier
        account.integration_status = "awaiting"
        self.store.save_account(account)
        await self.finish_integration(account)
        return self.store.get_account(account.account_id)

    async def finish_integration(self, account: LinkedAccount) -> bool:
        """PATCH status=completed (required after the POST). Safe to retry from /home."""
        try:
            await self.call(lambda ring: ring.set_integration_status("completed"), account)
        except (RingAPIError, TokenUnavailable) as exc:
            account = self.store.get_account(account.account_id)
            account.last_error = f"completing the integration failed: {exc}"
            self.store.save_account(account)
            logger.error("link: PATCH completed failed for %s: %s", token_label(account.account_id), exc)
            return False
        account = self.store.get_account(account.account_id)
        account.integration_status, account.last_error = "completed", None
        self.store.save_account(account)
        logger.info("link: integration completed for %s", token_label(account.account_id))
        return True

    # --- tokens ---------------------------------------------------------------------

    def active_account(self) -> LinkedAccount | None:
        linked = [a for a in self.store.list_accounts() if a.status == "linked" and a.access_token]
        return max(linked, key=lambda a: a.linked_at or 0) if linked else None

    async def refresh(self, account: LinkedAccount, reason: str) -> LinkedAccount:
        lock = self._locks.setdefault(account.account_id, asyncio.Lock())
        async with lock:
            current = self.store.get_account(account.account_id) or account
            if current.access_token != account.access_token and current.expires_at - self.now() > REFRESH_MARGIN_S:
                return current  # someone else refreshed while we waited
            if not current.refresh_token:
                raise TokenUnavailable(f"{token_label(current.account_id)} has no refresh token")
            try:
                tokens = await refresh_access_token(current.refresh_token, self.client_id, self.client_secret,
                                                    self.transport)
            except RingAPIError as exc:
                if exc.status_code in (400, 401):  # refresh token revoked or expired: needs re-linking
                    current.status, current.last_error = "needs_relink", f"token refresh failed: {exc}"
                    self.store.save_account(current)
                logger.error("token refresh (%s) failed for %s: %s", reason, token_label(current.account_id), exc)
                raise TokenUnavailable(str(exc)) from exc
            now = self.now()
            current.access_token = tokens["access_token"]
            current.refresh_token = tokens.get("refresh_token") or current.refresh_token  # they rotate
            current.expires_at, current.refreshed_at, current.last_error = now + float(tokens["expires_in"]), now, None
            self.store.save_account(current)
            logger.info("token refreshed (%s) for %s; expires in %ss", reason, token_label(current.account_id),
                        int(float(tokens["expires_in"])))
            return current

    async def token(self, account: LinkedAccount | None = None) -> tuple[str, str, LinkedAccount | None]:
        """(access token, source label, account) for the next API call."""
        account = account or self.active_account()
        if account is not None:
            try:
                if account.expires_at - self.now() < REFRESH_MARGIN_S:
                    account = await self.refresh(account, "expiring")
                return account.access_token, token_label(account.account_id), account
            except TokenUnavailable:
                if account.status != "unclaimed":
                    logger.warning("linked token unavailable for %s; falling back to the sandbox token",
                                   token_label(account.account_id))
                else:
                    raise
        sandbox = (self.sandbox_token() or "").strip()
        if sandbox:
            return sandbox, "sandbox", None
        raise TokenUnavailable("no Ring token: link an account or set RING_ACCESS_TOKEN")

    async def with_token(self, fn: Callable[[str, str], Awaitable[Any]], account: LinkedAccount | None = None) -> Any:
        """Run fn(token, source); on a 401 with a linked token, refresh once and retry."""
        token, source, acc = await self.token(account)
        try:
            return await fn(token, source)
        except RingTokenExpiredError:
            if acc is None:
                raise
            acc = await self.refresh(acc, "401")
            return await fn(acc.access_token, token_label(acc.account_id))

    async def call(self, fn: Callable[[RingClient], Awaitable[Any]], account: LinkedAccount | None = None) -> Any:
        async def run(token: str, source: str) -> Any:
            async with RingClient(token, source=source, transport=self.transport) as ring:
                return await fn(ring)

        return await self.with_token(run, account)

    async def refresh_due(self) -> list[str]:
        """Background job: refresh linked tokens that expire soon or haven't been refreshed in 24 h."""
        refreshed = []
        now = self.now()
        for acc in self.store.list_accounts():
            if acc.status != "linked" or not acc.refresh_token:
                continue
            stale = now - (acc.refreshed_at or acc.linked_at or acc.created_at) > SCHEDULED_REFRESH_S
            if acc.expires_at - now < 3 * REFRESH_MARGIN_S or stale:
                try:
                    await self.refresh(acc, "scheduled")
                    refreshed.append(acc.account_id)
                except TokenUnavailable:
                    pass
        return refreshed

    # --- disconnect / removal ---------------------------------------------------------

    async def disconnect(self, account_id: str) -> dict[str, Any]:
        """Partner-side disconnect for a one-way app.

        Ring's DELETE /v1/accounts/me/app-integrations is only for partner-initiated OAuth apps (one-way
        apps get 403), so we pause the integration (PATCH status=awaiting) and delete our tokens. The
        user removes DoorSight in the Ring app (or "Disconnect all" in the console) to revoke Ring's side.
        """
        account = self.store.get_account(account_id)
        if account is None:
            raise LookupError(f"no linked account {account_id}")
        paused = False
        if account.access_token:
            try:
                await self.call(lambda ring: ring.set_integration_status("awaiting"), account)
                paused = True
            except (RingAPIError, TokenUnavailable) as exc:
                logger.warning("disconnect: could not pause the integration for %s: %s", token_label(account_id), exc)
        self._wipe(account, "disconnected")
        logger.info("disconnect: tokens deleted for %s (Ring integration paused: %s)", token_label(account_id), paused)
        return {"account_id": account_id, "ring_integration_paused": paused}

    def removed_by_ring(self, account_id: str) -> bool:
        """app_integration_removed webhook: Ring already revoked the tokens; delete ours."""
        account = self.store.get_account(account_id)
        if account is None:
            return False
        self._wipe(account, "removed")
        logger.info("Ring removed the integration for %s; tokens deleted", token_label(account_id))
        return True

    def _wipe(self, account: LinkedAccount, status: str) -> None:
        account.access_token = account.refresh_token = None
        account.status, account.integration_status, account.expires_at = status, None, 0.0
        self.store.save_account(account)


_accounts: AccountService | None = None


def get_accounts() -> AccountService:
    global _accounts
    if _accounts is None:
        from backend.config import current_access_token, get_settings
        from backend.store import get_store

        s = get_settings()
        _accounts = AccountService(get_store(), client_id=s.ring_client_id, client_secret=s.ring_client_secret,
                                   hmac_key=s.ring_hmac_key, sandbox_token=current_access_token)
    return _accounts
