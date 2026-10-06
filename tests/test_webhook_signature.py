import json
import os

os.environ.setdefault("RING_CLIENT_ID", "test-client")
os.environ.setdefault("RING_CLIENT_SECRET", "test-secret")
os.environ["RING_HMAC_KEY"] = "test-hmac-key="
os.environ.setdefault("RING_ACCESS_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.ring.signatures import compute_nonce, compute_webhook_signature, verify_webhook_signature  # noqa: E402

KEY = "test-hmac-key="
BODY = json.dumps(
    {
        "meta": {"version": "1.1", "time": "2026-10-06T00:00:00Z", "request_id": "req-1", "account_id": "acct-1"},
        "data": {
            "id": "evt-1",
            "type": "motion_detected",
            "attributes": {"source": "dev-1", "source_type": "devices", "timestamp": 1791248000000},
            "relationships": {"devices": {"links": {"self": "/v1/devices/dev-1"}}},
        },
    },
    separators=(",", ":"),
).encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    log_file = tmp_path / "webhooks.jsonl"
    monkeypatch.setattr(type(main.settings), "webhook_log_file", property(lambda self: log_file))
    handled = []

    async def fake_handle_event(event):
        handled.append(event)

    monkeypatch.setattr(main, "handle_event", fake_handle_event)
    with TestClient(main.app) as c:
        c.log_file = log_file
        c.handled = handled
        yield c


def test_valid_signature_returns_200_and_logs_payload(client):
    sig = compute_webhook_signature(KEY, BODY)
    r = client.post("/webhook", content=BODY, headers={"X-Signature": sig, "Content-Type": "application/json"})
    assert r.status_code == 200
    lines = client.log_file.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["data"]["type"] == "motion_detected"
    assert [e.event_type for e in client.handled] == ["motion"]


def test_invalid_signature_returns_401(client):
    bad = compute_webhook_signature("wrong-key", BODY)
    r = client.post("/webhook", content=BODY, headers={"X-Signature": bad, "Content-Type": "application/json"})
    assert r.status_code == 401
    assert not client.log_file.exists()
    assert client.handled == []


def test_missing_signature_header_returns_401(client):
    r = client.post("/webhook", content=BODY, headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    assert not client.log_file.exists()
    assert client.handled == []


def test_signature_is_over_raw_bytes_not_reserialized_json():
    pretty = json.dumps(json.loads(BODY), indent=2).encode()
    sig = compute_webhook_signature(KEY, BODY)
    assert verify_webhook_signature(KEY, BODY, sig)
    assert not verify_webhook_signature(KEY, pretty, sig)


def test_key_is_used_as_utf8_not_base64_decoded():
    import hashlib
    import hmac

    expected = "sha256=" + hmac.new(KEY.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    assert compute_webhook_signature(KEY, BODY) == expected


def test_nonce_is_urlsafe_base64_without_padding():
    nonce = compute_nonce(KEY, "1771130906289", "acct-1")
    assert "=" not in nonce and "+" not in nonce and "/" not in nonce
    assert len(nonce) == 43  # 32-byte digest
