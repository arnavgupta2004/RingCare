import os

os.environ.setdefault("RING_CLIENT_ID", "test-client")
os.environ.setdefault("RING_CLIENT_SECRET", "test-secret")
os.environ.setdefault("RING_HMAC_KEY", "test-hmac-key=")
os.environ.setdefault("RING_ACCESS_TOKEN", "test-token")

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import doorstep as doorstep_mod  # noqa: E402
from backend import main  # noqa: E402
from backend.clock import DemoClock  # noqa: E402
from backend.doorstep import Doorstep, VisitProfile  # noqa: E402
from backend.store import SQLiteStore  # noqa: E402

REAL = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    ds = Doorstep(SQLiteStore(":memory:"), DemoClock(tz="Asia/Kolkata", real_now=lambda: REAL),
                  reminder_hours=3, profile=VisitProfile())
    monkeypatch.setattr(doorstep_mod, "_doorstep", ds)
    with TestClient(main.app) as c:
        c.ds = ds
        yield c


def test_clock_endpoint_sets_and_advances_and_triggers_reminders(client):
    r = client.post("/demo/clock", json={"set": "2026-10-06T14:10"})
    assert r.json()["sim_now"].startswith("2026-10-06T14:10")
    client.ds.record_event("e1", "package", analysis={
        "frame_count": 20, "detections": {"summary": {"package": {"frame_fraction": 1.0}}}})
    r = client.post("/demo/clock", json={"advance_hours": 3})
    body = r.json()
    assert body["sim_now"].startswith("2026-10-06T17:10")
    assert [n["kind"] for n in body["reminders_queued"]] == ["package_reminder"]
    assert client.post("/demo/clock", json={"set": "not a time"}).status_code == 422


def test_picked_up_endpoint(client):
    out = client.ds.record_event("e1", "package", analysis={
        "frame_count": 20, "detections": {"summary": {"package": {"frame_fraction": 1.0}}}})
    pid = out.package.id
    assert client.post(f"/packages/{pid}/picked-up").json()["status"] == "picked_up"
    assert client.post(f"/packages/{pid}/picked-up").status_code == 409
    assert client.post("/packages/pkg-nope/picked-up").status_code == 404
    state = client.get("/state").json()
    assert state["packages"][0]["status"] == "picked_up" and len(state["notifications"]) == 1
