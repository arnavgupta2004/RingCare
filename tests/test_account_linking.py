"""One-way account linking end to end against a fake Ring: nonce, token exchange, refresh, disconnect."""

import asyncio
import html
import logging
import os

os.environ.setdefault("RING_CLIENT_ID", "test-client")
os.environ.setdefault("RING_CLIENT_SECRET", "test-secret")
os.environ.setdefault("RING_HMAC_KEY", "test-hmac-key=")
os.environ.setdefault("RING_ACCESS_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, main  # noqa: E402
from backend.ring import accounts as accounts_mod  # noqa: E402
from backend.ring.accounts import AccountService, LinkError, TokenUnavailable  # noqa: E402
from backend.ring.client import RingTokenExpiredError  # noqa: E402
from tests.fake_ring import ACCOUNT, FakeRing  # noqa: E402

KEY = "test-hmac-key="
PASSWORD = "door-sight-demo-pw"
T0 = 1_791_250_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ring():
    return FakeRing(KEY)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(store, ring, clock):
    return AccountService(store, client_id="test-client", client_secret="test-secret", hmac_key=KEY,
                          sandbox_token=lambda: "sandbox-token", transport=ring.transport, now=clock)


def run(coro):
    return asyncio.run(coro)


def linked(svc, ring, clock):
    run(svc.receive_code("good-code"))
    nonce, t = ring.make_link(int(clock.t * 1000))
    clock.t += 30
    return run(svc.complete_link(nonce, t, "demo@doorsight.local"))


# --- token exchange --------------------------------------------------------------

def test_receive_code_stores_unclaimed_account_with_expiry(svc, ring, clock, caplog):
    caplog.set_level(logging.INFO)
    acc = run(svc.receive_code("good-code"))
    assert (acc.account_id, acc.status, acc.access_token, acc.refresh_token) == (ACCOUNT, "unclaimed", "at-1", "rt-1")
    assert acc.expires_at == T0 + 14400 and acc.scope == "ava.v1:read"
    assert svc.store.get_account(ACCOUNT) == acc
    assert [c[1] for c in ring.calls] == ["oauth/token", "/v1/users/me"]
    logs = caplog.text
    assert "at-1" not in logs and "rt-1" not in logs and "good-code" not in logs  # secrets stay out of logs
    assert "linked:…123456" in logs or "linked:new" in logs


def test_bad_code_is_rejected(svc):
    with pytest.raises(Exception, match="token exchange failed"):
        run(svc.receive_code("expired-code"))
    assert svc.store.list_accounts() == []


# --- nonce validation --------------------------------------------------------------

def test_nonce_matches_only_the_right_unclaimed_account(svc, ring, clock):
    run(svc.receive_code("good-code"))
    nonce, t = ring.make_link(int(clock.t * 1000))
    assert svc.match_nonce(nonce, t).account_id == ACCOUNT
    assert svc.match_nonce(nonce, str(int(t) + 1)) is None  # time is part of the HMAC
    assert svc.match_nonce(nonce[:-1] + ("A" if nonce[-1] != "A" else "B"), t) is None
    clock.t += 16 * 60
    assert svc.match_nonce(nonce, t) is None  # unclaimed tokens expire after 15 min


@pytest.mark.parametrize("offset_s,ok", [(-599, True), (-601, False), (+5, False)])
def test_link_time_window(svc, ring, clock, offset_s, ok):
    run(svc.receive_code("good-code"))
    nonce, t = ring.make_link(int((clock.t + offset_s) * 1000))
    if ok:
        assert run(svc.complete_link(nonce, t, "demo@doorsight.local")).status == "linked"
    else:
        with pytest.raises(LinkError, match="expired or is invalid"):
            run(svc.complete_link(nonce, t, "demo@doorsight.local"))
        assert ("POST", "/v1/accounts/me/app-integrations") not in [c[:2] for c in ring.calls]


def test_complete_link_posts_nonce_then_patches_completed(svc, ring, clock):
    acc = linked(svc, ring, clock)
    api = [c for c in ring.calls if c[1] == "/v1/accounts/me/app-integrations"]
    assert api[0][0] == "POST" and api[0][2]["account_identifier"] == "d***o@doorsight.local"
    assert api[0][2]["nonce"] == ring.make_link(int(ring.link_time_ms))[0]
    assert api[1] == ("PATCH", "/v1/accounts/me/app-integrations", {"status": "completed"})
    assert (acc.status, acc.integration_status, acc.partner_user) == ("linked", "completed", "demo@doorsight.local")
    assert ring.integration_status == "completed"
    with pytest.raises(LinkError):  # a nonce can't claim an already-linked account
        run(svc.complete_link(*ring.make_link(int(clock.t * 1000)), "demo@doorsight.local"))


def test_ring_rejecting_the_nonce_and_failed_patch(svc, ring, clock):
    run(svc.receive_code("good-code"))
    nonce, t = ring.make_link(int(clock.t * 1000))
    ring.link_time_ms = str(int(t) - 1)  # Ring's own record differs -> Ring says Invalid Nonce
    with pytest.raises(LinkError, match="Ring could not confirm"):
        run(svc.complete_link(nonce, t, "demo@doorsight.local"))
    assert svc.store.get_account(ACCOUNT).status == "unclaimed"

    ring.link_time_ms, ring.fail_patch = t, True
    acc = run(svc.complete_link(nonce, t, "demo@doorsight.local"))
    assert acc.status == "linked" and acc.integration_status == "awaiting" and "completing" in acc.last_error
    ring.fail_patch = False
    assert run(svc.finish_integration(acc)) and svc.store.get_account(ACCOUNT).integration_status == "completed"


# --- refresh and token selection -------------------------------------------------

def test_sandbox_token_when_nothing_is_linked(svc):
    token, source, acc = run(svc.token())
    assert (token, source, acc) == ("sandbox-token", "sandbox", None)
    empty = AccountService(svc.store, client_id="c", client_secret="s", hmac_key=KEY, sandbox_token=lambda: "")
    with pytest.raises(TokenUnavailable):
        run(empty.token())


def test_linked_token_is_preferred_and_refreshed_before_expiry(svc, ring, clock, caplog):
    caplog.set_level(logging.INFO)
    linked(svc, ring, clock)
    assert run(svc.token())[:2] == ("at-1", "linked:…123456")
    clock.t = T0 + 14400 - 120  # 2 minutes left
    token, source, acc = run(svc.token())
    assert token == "at-2" and acc.refresh_token == "rt-2" and acc.refreshed_at == clock.t
    assert "rt-1" not in ring.refresh  # rotated
    assert "token refreshed (expiring) for linked:…123456" in caplog.text
    assert "at-2" not in caplog.text and "rt-2" not in caplog.text


def test_401_triggers_one_refresh_and_retry(svc, ring, clock, caplog):
    caplog.set_level(logging.INFO)
    linked(svc, ring, clock)
    ring.revoke_access("at-1")  # e.g. revoked server-side before expiry
    devices = run(svc.call(lambda r: r.list_devices()))
    assert devices["data"][0]["id"] == "dev-1"
    assert svc.store.get_account(ACCOUNT).access_token == "at-2"
    assert "[token: linked:…123456]" in caplog.text and "token refreshed (401)" in caplog.text

    ring.revoke_access("at-2")
    ring.access.clear()  # refresh issues a token that also fails -> no endless loop
    ring._issue = lambda: {"access_token": "dead", "refresh_token": "rt-x", "expires_in": 14400}
    with pytest.raises(RingTokenExpiredError):
        run(svc.call(lambda r: r.list_devices()))


def test_failed_refresh_marks_needs_relink_and_falls_back_to_sandbox(svc, ring, clock, caplog):
    linked(svc, ring, clock)
    ring.refresh.clear()  # refresh token revoked
    clock.t = T0 + 14400
    token, source, _ = run(svc.token())
    assert (token, source) == ("sandbox-token", "sandbox")
    acc = svc.store.get_account(ACCOUNT)
    assert acc.status == "needs_relink" and "token refresh failed" in acc.last_error
    assert svc.active_account() is None


def test_scheduled_refresh(svc, ring, clock):
    linked(svc, ring, clock)
    assert run(svc.refresh_due()) == []
    clock.t += 25 * 3600  # stale after 24 h even if... (also expired by now)
    assert run(svc.refresh_due()) == [ACCOUNT]


def test_with_token_retries_capture_style_calls(svc, ring, clock):
    linked(svc, ring, clock)
    seen = []

    async def capture(token, source):
        seen.append((token, source))
        if token == "at-1":
            raise RingTokenExpiredError()
        return "frames"
    assert run(svc.with_token(capture)) == "frames"
    assert seen == [("at-1", "linked:…123456"), ("at-2", "linked:…123456")]


# --- disconnect / removal ------------------------------------------------------------

def test_disconnect_pauses_integration_and_wipes_tokens(svc, ring, clock):
    linked(svc, ring, clock)
    result = run(svc.disconnect(ACCOUNT))
    assert result == {"account_id": ACCOUNT, "ring_integration_paused": True}
    assert ring.calls[-1] == ("PATCH", "/v1/accounts/me/app-integrations", {"status": "awaiting"})
    acc = svc.store.get_account(ACCOUNT)
    assert (acc.status, acc.access_token, acc.refresh_token) == ("disconnected", None, None)
    assert run(svc.token())[1] == "sandbox"


def test_disconnect_still_wipes_when_ring_is_unreachable(svc, ring, clock):
    linked(svc, ring, clock)
    ring.fail_patch = True
    assert run(svc.disconnect(ACCOUNT))["ring_integration_paused"] is False
    assert svc.store.get_account(ACCOUNT).access_token is None


def test_ring_removal_webhook_wipes_tokens(svc, ring, clock, monkeypatch):
    from backend import events

    linked(svc, ring, clock)
    monkeypatch.setattr(accounts_mod, "_accounts", svc)
    payload = {"meta": {"version": "1.1", "account_id": ACCOUNT},
               "data": {"id": "e1", "type": "app_integration_removed",
                        "attributes": {"source": ACCOUNT, "source_type": "users", "timestamp": 1}}}
    record = run(events.handle_event(events.normalize(payload)))
    assert record["lifecycle"] == {"action": "tokens_deleted"}
    acc = svc.store.get_account(ACCOUNT)
    assert acc.status == "removed" and acc.access_token is None


# --- HTTP: /token, /link sign-in, /home, disconnect -----------------------------------

@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.setattr(accounts_mod, "_accounts", svc)
    monkeypatch.setattr(main, "get_accounts", lambda: svc)
    monkeypatch.setattr(main, "login_throttle", auth.LoginThrottle())
    monkeypatch.setenv("DEMO_USER_EMAIL", "demo@doorsight.local")
    monkeypatch.setenv("DEMO_USER_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main, "_now_ms", lambda: int(svc.now() * 1000))
    with TestClient(main.app) as c:
        yield c


def test_token_endpoint(client, svc):
    assert client.post("/token", data={}).status_code == 400
    assert client.post("/token", data={"code": "bad"}).status_code == 502
    assert client.post("/token", data={"code": "good-code"}).json() == {"status": "ok"}
    assert svc.store.get_account(ACCOUNT).status == "unclaimed"


def test_link_requires_sign_in_before_nonce_matching(client, svc, ring, clock):
    client.post("/token", data={"code": "good-code"})
    nonce, t = ring.make_link(int(clock.t * 1000))
    page = client.get("/link", params={"nonce": nonce, "time": t})
    assert page.status_code == 200 and 'type="password"' in page.text
    assert not any(c[1] == "/v1/accounts/me/app-integrations" for c in ring.calls)  # nothing before sign-in

    bad = client.post("/link", data={"nonce": nonce, "time": t, "email": "demo@doorsight.local", "password": "nope"})
    assert bad.status_code == 401 and "don't match" in html.unescape(bad.text)
    assert svc.store.get_account(ACCOUNT).status == "unclaimed"

    ok = client.post("/link", data={"nonce": nonce, "time": t, "email": "demo@doorsight.local", "password": PASSWORD})
    assert ok.status_code == 200 and "Your Ring account is linked" in ok.text
    assert auth.COOKIE_NAME in client.cookies
    acc = svc.store.get_account(ACCOUNT)
    assert (acc.status, acc.integration_status, acc.account_identifier) == ("linked", "completed",
                                                                            "d***o@doorsight.local")


def test_signed_in_user_links_with_one_click_and_stale_links_are_rejected(client, svc, ring, clock):
    client.post("/signin", data={"email": "demo@doorsight.local", "password": PASSWORD})
    client.post("/token", data={"code": "good-code"})
    nonce, t = ring.make_link(int(clock.t * 1000))
    page = client.get("/link", params={"nonce": nonce, "time": t})
    assert "Link my Ring account" in page.text and 'type="password"' not in page.text
    assert client.post("/link", data={"nonce": nonce, "time": t}).status_code == 200
    stale = client.get("/link", params={"nonce": nonce, "time": str(int(t) - 601_000)})
    assert stale.status_code == 400 and "rejected" in stale.text
    assert client.get("/link").status_code == 400


def test_sign_in_is_throttled(client):
    for _ in range(5):
        client.post("/signin", data={"email": "demo@doorsight.local", "password": "wrong"})
    r = client.post("/signin", data={"email": "demo@doorsight.local", "password": PASSWORD})
    assert r.status_code == 401 and auth.COOKIE_NAME not in client.cookies


def test_home_shows_status_and_disconnect_works(client, svc, ring, clock):
    linked(svc, ring, clock)
    anon = client.get("/home")
    assert "Ring: connected" in anon.text and ACCOUNT not in anon.text and "Disconnect" not in anon.text
    assert client.post("/home/disconnect", data={"account_id": ACCOUNT}).status_code == 401

    client.post("/signin", data={"email": "demo@doorsight.local", "password": PASSWORD})
    home = client.get("/home").text
    assert ACCOUNT in home and "Access token expires" in home and "Disconnect" in home
    assert "linked:…123456" in home and "at-1" not in home  # token values never rendered

    blocked = client.post("/home/disconnect", data={"account_id": ACCOUNT},
                          headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403 and svc.store.get_account(ACCOUNT).access_token

    done = client.post("/home/disconnect", data={"account_id": ACCOUNT})
    assert done.status_code == 200 and "Disconnected. DoorSight deleted its Ring tokens" in done.text
    assert svc.store.get_account(ACCOUNT).status == "disconnected"
    assert "Ring: no account linked yet" in client.get("/home").text


def test_finish_setup_button(client, svc, ring, clock):
    ring.fail_patch = True
    linked(svc, ring, clock)
    client.post("/signin", data={"email": "demo@doorsight.local", "password": PASSWORD})
    assert "Finish setup" in client.get("/home").text
    ring.fail_patch = False
    assert "Setup finished." in client.post("/home/complete", data={"account_id": ACCOUNT}).text
