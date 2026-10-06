import os

os.environ.setdefault("RING_CLIENT_ID", "test-client")
os.environ.setdefault("RING_CLIENT_SECRET", "test-secret")
os.environ.setdefault("RING_HMAC_KEY", "test-hmac-key=")
os.environ.setdefault("RING_ACCESS_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.events import build_simulated_payload, normalize  # noqa: E402


def test_normalize_motion_uses_subtype():
    payload = {
        "meta": {"version": "1.1", "account_id": "acct-1"},
        "data": {"id": "e1", "type": "motion_detected", "subType": "vehicle",
                 "attributes": {"source": "dev-1", "timestamp": 1700000000000}},
    }
    event = normalize(payload)
    assert (event.event_id, event.event_type, event.device_id, event.account_id) == ("e1", "vehicle", "dev-1", "acct-1")
    assert event.timestamp_ms == 1700000000000


def test_normalize_motion_without_subtype_defaults_to_motion():
    event = normalize({"data": {"id": "e2", "type": "motion_detected", "attributes": {}}})
    assert event.event_type == "motion"


def test_normalize_non_motion_keeps_ring_type():
    assert normalize({"data": {"type": "button_press", "attributes": {}}}).event_type == "button_press"


@pytest.mark.parametrize("kind", ["package", "vehicle", "motion"])
def test_simulated_payload_round_trips_through_normalize(kind):
    event = normalize(build_simulated_payload(kind, "dev-9"), source="simulated")
    assert event.event_type == kind
    assert event.device_id == "dev-9"
    assert event.event_id.startswith(f"sim-{kind}-")
    assert event.source == "simulated"


@pytest.fixture
def client(monkeypatch):
    handled = []

    async def fake_handle_event(event):
        handled.append(event)
        return {"event_id": event.event_id}

    monkeypatch.setattr(main, "handle_event", fake_handle_event)
    with TestClient(main.app) as c:
        c.handled = handled
        yield c


def test_simulate_event_accepts_and_dispatches(client):
    r = client.post("/simulate-event", json={"event_type": "package"})
    assert r.status_code == 202
    assert r.json()["presenter_hint"] == 'click "Package" in the Ring Playground first'
    assert [e.event_type for e in client.handled] == ["package"]


def test_simulate_event_rejects_unknown_type(client):
    r = client.post("/simulate-event", json={"event_type": "dragon"})
    assert r.status_code == 422
    assert client.handled == []
