"""Brain selection and the agent-facing API routes."""

import os

os.environ.setdefault("RING_CLIENT_ID", "test-client")
os.environ.setdefault("RING_CLIENT_SECRET", "test-secret")
os.environ.setdefault("RING_HMAC_KEY", "test-hmac-key=")
os.environ.setdefault("RING_ACCESS_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import doorstep as doorstep_mod  # noqa: E402
from backend import events as events_mod  # noqa: E402
from backend import main  # noqa: E402
from backend.agent import RulesBrain, runner as runner_mod  # noqa: E402
from backend.store import SQLiteStore  # noqa: E402
from tests.agent_helpers import make_runner  # noqa: E402


@pytest.mark.parametrize("setting,available,expected", [
    ("rules", True, "rules"), ("bedrock", False, "bedrock"), ("auto", True, "bedrock"), ("auto", False, "rules"),
])
def test_choose_brain(monkeypatch, setting, available, expected):
    calls = []
    monkeypatch.setattr(runner_mod, "bedrock_available", lambda: calls.append(1) or (available, "detail"))
    choice = runner_mod.choose_brain(setting)
    assert choice.brain.name == expected
    assert calls == ([1] if setting == "auto" else [])
    if setting == "auto" and not available:
        assert "Bedrock unavailable" in choice.why


def test_choose_brain_rejects_unknown():
    with pytest.raises(ValueError):
        runner_mod.choose_brain("gpt")


def test_bedrock_brain_uses_haiku_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_ID", raising=False)
    brain = runner_mod.bedrock_brain()
    assert brain.name == "bedrock" and brain.model.get_config()["model_id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_check_script_result_is_parsed(monkeypatch):
    class Proc:
        returncode, stdout = 1, "FAIL  account ...\n-> The account cannot invoke any Bedrock model."
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Proc())
    assert runner_mod.bedrock_available() == (False, "-> The account cannot invoke any Bedrock model.")


@pytest.fixture
def client(monkeypatch):
    async def no_frames(*a):
        return None
    runner = make_runner(SQLiteStore(":memory:"), RulesBrain(), why="test", capture=no_frames)
    monkeypatch.setattr(runner_mod, "_runner", runner)
    monkeypatch.setattr(doorstep_mod, "_doorstep", runner.doorstep)
    monkeypatch.setattr(main, "select_brain_at_startup", lambda: runner.info())
    with TestClient(main.app) as c:
        c.runner = runner
        yield c


def test_simulate_event_goes_through_the_agent(client):
    r = client.post("/simulate-event", json={"event_type": "vehicle", "wait": True})
    agent = r.json()["agent"]
    assert agent["brain"] == "rules"
    assert agent["tools"][:2] == ["start_live_capture (error)", "get_package_state"]
    ev = client.get("/state").json()["events"][0]
    assert ev["agent_brain"] == "rules" and ev["agent_reason"].startswith("No usable capture")
    assert [t["tool"] for t in ev["agent_trace"]][-1] == "get_visit_baseline"


def test_digest_endpoint_and_agent_info(client):
    r = client.post("/digest", json={}).json()
    assert r["brain"] == "rules" and r["notification"]["kind"] == "daily_digest"
    assert r["notification"]["text"].startswith("DoorSight daily summary:")
    state = client.get("/state").json()
    assert state["agent"] == {"brain": "rules", "why": "test"}
    assert state["notifications"][-1]["kind"] == "daily_digest"


def test_webhook_events_use_the_agent(client, monkeypatch):
    seen = []
    original = client.runner.handle_event

    async def fake_handle(event_id, event_type, **kw):
        seen.append((event_type, kw["source"]))
        return await original(event_id, event_type, **kw)
    monkeypatch.setattr(client.runner, "handle_event", fake_handle)
    payload = {"data": {"id": "evt-1", "type": "motion_detected", "subType": "vehicle", "attributes": {}}}
    import asyncio
    asyncio.run(events_mod.handle_event(events_mod.normalize(payload)))
    assert seen == [("vehicle", "webhook")]


def test_demo_replay_runs_a_capture_through_the_agent(client, tmp_path, monkeypatch):
    import json as _json

    from tests.agent_helpers import analysis

    frames = main.settings.data_dir / "frames" / "test-replay-capture"
    frames.mkdir(parents=True, exist_ok=True)
    from backend.vision import analyze as analyze_mod
    an_path = tmp_path / "analysis.json"
    an_path.write_text(_json.dumps(analysis(package=0.95)))
    monkeypatch.setattr(analyze_mod, "analysis_path", lambda capture: an_path)
    try:
        r = client.post("/demo/replay", json={"event_type": "package", "capture": "test-replay-capture"}).json()
        assert r["package_action"] == "created" and r["brain"] == "rules"
        assert r["notifications"][0]["kind"] == "package_arrived"
        ev = client.get("/state").json()["events"][0]
        assert ev["source"] == "replay" and ev["event_type"] == "package"
        assert client.post("/demo/replay", json={"event_type": "dragon"}).status_code == 422
        assert client.post("/demo/replay", json={"event_type": "package", "capture": "nope"}).status_code == 404
    finally:
        frames.rmdir()
