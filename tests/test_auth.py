import pytest

from backend import auth


def test_password_hash_round_trip_and_rejections():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$16384$8$1$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("x", None) and not auth.verify_password("x", "md5$nope")
    assert auth.hash_password("same") != auth.hash_password("same")  # salted


def test_credentials_check(monkeypatch):
    monkeypatch.setenv("DEMO_USER_EMAIL", "Demo@DoorSight.local")
    monkeypatch.setenv("DEMO_USER_PASSWORD_HASH", auth.hash_password("pw-1234567890"))
    assert auth.check_credentials(" demo@doorsight.LOCAL ", "pw-1234567890")
    assert not auth.check_credentials("other@doorsight.local", "pw-1234567890")
    assert not auth.check_credentials("demo@doorsight.local", "pw-wrong")


def test_session_cookie_sign_verify_tamper_expire(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s3cret")
    cookie = auth.make_session("demo@doorsight.local", now=1000)
    assert auth.read_session(cookie, now=1001) == "demo@doorsight.local"
    payload, sig = cookie.rsplit(".", 1)
    assert auth.read_session(payload + "." + sig[:-2] + "AA", now=1001) is None
    assert auth.read_session(cookie, now=1000 + auth.SESSION_SECONDS + 1) is None
    monkeypatch.setenv("SESSION_SECRET", "rotated")
    assert auth.read_session(cookie, now=1001) is None
    assert auth.read_session(None) is None and auth.read_session("garbage") is None


def test_login_throttle():
    t = auth.LoginThrottle(max_failures=3, window_s=60)
    for i in range(3):
        assert not t.blocked("ip", now=100 + i)
        t.fail("ip", now=100 + i)
    assert t.blocked("ip", now=110) and not t.blocked("other", now=110)
    assert not t.blocked("ip", now=200)  # window passed
    t.fail("ip", now=300)
    t.reset("ip")
    assert not t.blocked("ip", now=301)


@pytest.mark.parametrize("email,masked", [("user@partner.example.com", "u***r@partner.example.com"),
                                          ("ab@x.io", "a***@x.io"), ("demo@doorsight.local", "d***o@doorsight.local")])
def test_mask_email(email, masked):
    assert auth.mask_email(email) == masked
