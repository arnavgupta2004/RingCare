"""The real Strands agent loop, driven by a scripted fake model (no Bedrock)."""

import asyncio
from datetime import datetime

import pytest

from backend.agent import RulesBrain, StrandsBrain
from tests.agent_helpers import DRIVEWAY, analysis, make_runner
from tests.fakes import ScriptedModel

ARRIVAL = [
    {"tools": [("start_live_capture", {}), ("describe_scene", {})]},
    {"tools": [("get_package_state", {})]},
    {"tools": [("update_package_state", {"action": "create", "reason": "A parcel is on the step."})]},
    {"tools": [("notify_resident", {"text": "A package was left at your door at 2:10 PM.",
                                    "kind": "package_arrived"})]},
    {"text": "A parcel appeared on the step (detector estimate); I recorded it and told the resident."},
]


def agent_runner(store, turns, **kw):
    model = ScriptedModel(turns)
    return make_runner(store, StrandsBrain(model, name="bedrock", **kw)), model


def test_agent_handles_arrival_through_tools(store):
    runner, model = agent_runner(store, ARRIVAL)
    ctx = asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))

    assert ctx.brain == "bedrock" and ctx.package_action == "created"
    assert ctx.reason.startswith("A parcel appeared on the step")
    assert [t["tool"] for t in ctx.trace] == ["start_live_capture", "describe_scene", "get_package_state",
                                               "update_package_state", "notify_resident"]
    assert all(t["ok"] and t["brain"] == "bedrock" for t in ctx.trace)
    ev = runner.doorstep.store.get_event("pkg")
    assert ev.agent_brain == "bedrock" and ev.agent_reason == ctx.reason and ev.package_check == "new package at the door"
    [n] = runner.doorstep.store.list_notifications()
    assert (n.audience, n.kind, n.source) == ("resident", "package_arrived", "bedrock")
    # what the model was given
    first = model.requests[0]
    assert sorted(first["tools"]) == sorted(["start_live_capture", "describe_scene", "get_package_state",
                                             "update_package_state", "get_visit_baseline", "notify_resident",
                                             "notify_caregiver", "write_daily_digest"])
    assert "Never claim more certainty than the evidence supports" in first["system_prompt"]
    assert "'package' event" in first["messages"][0]["content"][0]["text"]


def test_refused_actions_come_back_as_tool_errors_and_change_nothing(store):
    runner, _ = agent_runner(store, ARRIVAL)
    asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))

    runner.brain, model = StrandsBrain(ScriptedModel([
        {"tools": [("start_live_capture", {}), ("describe_scene", {}), ("get_package_state", {})]},
        {"tools": [("update_package_state", {"action": "mark_missing", "reason": "No box in view."}),
                   ("notify_caregiver", {"text": "The package was stolen!", "kind": "package_missing"})]},
        {"tools": [("update_package_state", {"action": "no_change", "reason": "Different camera view."})]},
        {"text": "This capture is from a different camera, so I could not check the package."},
    ]), name="bedrock"), None
    model = runner.brain.model
    ctx = asyncio.run(runner.handle_event("veh", "vehicle", preloaded_analysis=analysis(vehicle=0.9, view=DRIVEWAY)))

    failed = [t for t in ctx.trace if not t["ok"]]
    assert [t["tool"] for t in failed] == ["update_package_state", "notify_caregiver"]
    assert "not allowed" in failed[0]["error"] and "marked missing" in failed[1]["error"]
    results = model.tool_results()
    assert [r["status"] for r in results][-3:] == ["error", "error", "success"]
    assert "not allowed" in results[-3]["content"][0]["text"]
    assert runner.doorstep.store.open_package().status.value == "present"
    assert runner.doorstep.store.list_notifications("caregiver") == []
    assert runner.doorstep.store.get_event("veh").package_check == "different view — can't verify package"


def test_unusual_hour_alert_requires_baseline_first(store):
    runner, _ = agent_runner(store, [
        {"tools": [("start_live_capture", {}), ("describe_scene", {})]},
        {"tools": [("notify_caregiver", {"text": "Car at 3 AM.", "kind": "unusual_hour"})]},
        {"tools": [("get_visit_baseline", {})]},
        {"tools": [("notify_caregiver", {"text": "A vehicle was at the door at 3:00 AM; score 0.9 vs 0.7.",
                                         "kind": "unusual_hour"})]},
        {"text": "Vehicle at a quiet hour (score 0.9); caregiver alerted."},
    ])
    runner.doorstep.clock.set(datetime(2026, 10, 7, 3, 0))
    ctx = asyncio.run(runner.handle_event("night", "vehicle", preloaded_analysis=analysis(vehicle=0.9)))
    assert [t["ok"] for t in ctx.trace if t["tool"] == "notify_caregiver"] == [False, True]
    [alert] = runner.doorstep.store.list_notifications("caregiver")
    assert alert.kind == "unusual_hour" and alert.extra["score"] == 0.9 and alert.source == "bedrock"
    assert runner.doorstep.store.get_event("night").unusual_score == 0.9


def test_model_failure_falls_back_to_rules_without_duplicates(store):
    runner, _ = agent_runner(store, [
        {"tools": [("start_live_capture", {}), ("describe_scene", {}), ("get_package_state", {})]},
        {"tools": [("update_package_state", {"action": "create", "reason": "parcel"})]},
        {"tools": [("notify_resident", {"text": "A package was left at your door at 2:10 PM.",
                                        "kind": "package_arrived"})]},
        {"raise": RuntimeError("ThrottlingException: Rate exceeded")},
    ])
    ctx = asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))

    assert ctx.brain == "rules"
    # Strands wraps model errors (EventLoopException); the original message must survive.
    assert ctx.reason.startswith("[bedrock agent failed (") and "ThrottlingException: Rate exceeded" in ctx.reason
    assert "handled by rules] Capture shows package" in ctx.reason
    agent_err = next(t for t in ctx.trace if t["tool"] == "agent")
    assert not agent_err["ok"] and "Rate exceeded" in agent_err["error"]
    store_ = runner.doorstep.store
    assert len(store_.list_packages()) == 1 and len(store_.list_notifications()) == 1  # nothing duplicated
    ev = store_.get_event("pkg")
    assert ev.agent_brain == "rules" and {t["brain"] for t in ev.agent_trace} == {"bedrock", "rules"}


def test_model_timeout_falls_back_to_rules(store):
    runner, _ = agent_runner(store, [{"sleep": 2}], timeout_s=0.2)
    ctx = asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))
    assert ctx.brain == "rules" and "TimeoutError" in ctx.reason
    assert ctx.package_action == "created" and len(runner.doorstep.store.list_notifications()) == 1


def test_agent_that_does_nothing_still_records_the_event(store):
    runner, _ = agent_runner(store, [{"text": "Routine daytime activity; nothing to report."}])
    ctx = asyncio.run(runner.handle_event("veh", "vehicle", preloaded_analysis=analysis(vehicle=0.9)))
    ev = runner.doorstep.store.get_event("veh")
    assert ev.agent_reason == "Routine daytime activity; nothing to report." and ev.agent_trace == []
    assert ev.unusual_score == 0.0  # recorded even though the agent didn't ask
    assert ctx.notifications == []


def test_rules_brain_failure_is_not_swallowed(store):
    class Broken(RulesBrain):
        async def run_event(self, tools, ctx):
            raise RuntimeError("bug")
    runner = make_runner(store, Broken())
    with pytest.raises(RuntimeError, match="bug"):
        asyncio.run(runner.handle_event("e", "motion"))
    assert runner.doorstep.store.get_event("e") is not None  # the event is still saved


def test_agent_digest_and_fallback_when_tool_not_called(store):
    runner, _ = agent_runner(store, [
        {"tools": [("write_daily_digest", {"note": "A quiet day apart from one delivery."})]},
        {"text": "Digest sent."},
    ])
    ctx = asyncio.run(runner.write_digest())
    [d] = runner.doorstep.store.list_notifications("caregiver")
    assert d.kind == "daily_digest" and d.source == "bedrock" and d.text.endswith("Note: A quiet day apart from one delivery.")
    assert ctx.brain == "bedrock"

    runner.brain = StrandsBrain(ScriptedModel([{"text": "Done!"}]), name="bedrock")
    ctx = asyncio.run(runner.write_digest())
    assert ctx.brain == "rules" and "did not call write_daily_digest" in ctx.reason
    assert len(runner.doorstep.store.list_notifications("caregiver")) == 2
