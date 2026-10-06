"""Tool guard rails, called directly (as the rules brain does). Runs on SQLite and DynamoDB."""

import asyncio

import pytest

from backend.agent import DoorstepTools, EventContext, ToolError
from tests.agent_helpers import DRIVEWAY, analysis, make_doorstep


def run(coro):
    return asyncio.run(coro)


def tools_for(ds, event_type="package", an=None, **kw):
    ctx = EventContext(event_id=f"e-{event_type}", event_type=event_type, preloaded_analysis=an, **kw)
    return DoorstepTools(ds, ctx), ctx


@pytest.fixture
def ds(store):
    return make_doorstep(store)


def test_capture_and_describe_use_existing_capture(ds):
    t, ctx = tools_for(ds, an=analysis(package=0.95))
    assert run(t.start_live_capture()) == {"frames": 20, "source": "existing capture"}
    scene = run(t.describe_scene())
    assert scene["seen"] == {"package": True, "vehicle": False, "person": False}
    assert scene["description_source"] == "stub" and scene["share_of_frames"]["package"] == 0.95


def test_capture_refusals(ds):
    t, _ = tools_for(ds, event_type="button_press_x")
    with pytest.raises(ToolError, match="no video"):
        run(t.start_live_capture())
    t, _ = tools_for(ds, event_type="vehicle")
    with pytest.raises(ToolError, match="not available"):
        run(t.start_live_capture())
    with pytest.raises(ToolError, match="call start_live_capture first"):
        run(t.describe_scene())


def test_live_capture_and_analyze_are_called_once(ds, tmp_path):
    calls = []
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(3):
        (frames / f"frame_{i:03d}.jpg").write_bytes(b"x")

    async def capture(event_id, event_type, device_id):
        calls.append("capture")
        return frames

    def analyze(event_id, event_type, frames_dir):
        calls.append("analyze")
        return analysis(vehicle=0.9)

    ctx = EventContext(event_id="e1", event_type="vehicle")
    t = DoorstepTools(ds, ctx, capture=capture, analyze=analyze)
    assert run(t.start_live_capture())["frames"] == 3
    run(t.start_live_capture())
    run(t.describe_scene())
    run(t.describe_scene())
    assert calls == ["capture", "analyze"]


def test_capture_without_frames_is_an_error(ds):
    async def capture(*a):
        return None
    t = DoorstepTools(ds, EventContext(event_id="e1", event_type="motion"), capture=capture)
    with pytest.raises(ToolError, match="did not return any frames"):
        run(t.start_live_capture())


def test_package_update_requires_state_and_allowed_action(ds):
    t, ctx = tools_for(ds, an=analysis(package=0.95))
    run(t.describe_scene())
    with pytest.raises(ToolError, match="get_package_state first"):
        t.update_package_state("create", "x")
    state = t.get_package_state()
    assert state["recommended_action"] == "create" and state["allowed_actions"] == ["create", "no_change"]
    with pytest.raises(ToolError, match="not allowed"):
        t.update_package_state("mark_missing", "x")
    with pytest.raises(ToolError, match="unknown action"):
        t.update_package_state("explode", "x")
    assert t.update_package_state("create", "new parcel")["status"] == "applied"
    assert t.update_package_state("create", "again")["status"] == "already_applied"
    assert len(ds.store.list_packages()) == 1


def test_different_view_only_allows_no_change(ds):
    t, _ = tools_for(ds, an=analysis(package=0.95))
    run(t.describe_scene())
    t.get_package_state()
    t.update_package_state("create", "x")
    t2, _ = tools_for(ds, event_type="vehicle", an=analysis(vehicle=0.9, view=DRIVEWAY))
    run(t2.describe_scene())
    state = t2.get_package_state()
    assert state["allowed_actions"] == ["no_change"] and state["check"] == "different view — can't verify package"
    with pytest.raises(ToolError, match="not allowed"):
        t2.update_package_state("mark_missing", "it's gone!")
    assert ds.store.open_package().status.value == "present"


def test_notification_guards(ds):
    t, ctx = tools_for(ds, an=analysis(package=0.95))
    run(t.describe_scene())
    t.get_package_state()
    with pytest.raises(ToolError, match="needs a package created"):
        t.notify_resident("A package arrived.", "package_arrived")
    with pytest.raises(ToolError, match="kind must be"):
        t.notify_resident("hi", "alarm")
    with pytest.raises(ToolError, match="empty"):
        t.notify_resident("  ", "info")
    with pytest.raises(ToolError, match="too long"):
        t.notify_resident("x" * 601, "info")
    with pytest.raises(ToolError, match="marked missing"):
        t.notify_caregiver("gone", "package_missing")
    with pytest.raises(ToolError, match="get_visit_baseline first"):
        t.notify_caregiver("odd hour", "unusual_hour")
    t.update_package_state("create", "x")
    first = t.notify_resident("A package was left at 2:10 PM.", "package_arrived")
    again = t.notify_resident("Another message.", "package_arrived")
    assert again == {"status": "already_sent", "notification_id": first["notification_id"]}
    [n] = ds.store.list_notifications()
    assert n.source == ctx.brain == "rules" and n.extra["observation_source"] == "stub"
    assert n.package_id == ctx.assessment.open_package.id


def test_visit_baseline_not_applicable_for_package_events(ds):
    t, _ = tools_for(ds)
    assert t.get_visit_baseline() == {"applicable": False, "reason": "'package' events are not scored"}


def test_trace_records_inputs_outputs_and_errors(ds):
    t, ctx = tools_for(ds, an=analysis(package=0.95))
    run(t.describe_scene())
    with pytest.raises(ToolError):
        t.update_package_state("create", "too early")
    ok, failed = ctx.trace
    assert ok["tool"] == "describe_scene" and ok["ok"] and "package" in ok["output"] and ok["ms"] >= 0
    assert failed == {**failed, "tool": "update_package_state", "ok": False,
                      "input": {"action": "create", "reason": "too early"}, "brain": "rules"}
    assert "get_package_state first" in failed["error"]


def test_tool_specs_are_clean():
    t = DoorstepTools.__new__(DoorstepTools)
    specs = {f.tool_spec["name"]: f.tool_spec for f in DoorstepTools.all(t)}
    assert set(specs) == {"start_live_capture", "describe_scene", "get_package_state", "update_package_state",
                          "get_visit_baseline", "notify_resident", "notify_caregiver", "write_daily_digest"}
    props = specs["update_package_state"]["inputSchema"]["json"]["properties"]
    assert set(props) == {"action", "reason"} and "self" not in props
