"""The rules brain must make exactly the decisions of the reference doorstep rules (record_event)."""

import asyncio
from datetime import datetime

import pytest

from backend.agent import RulesBrain
from backend.store import SQLiteStore
from tests.agent_helpers import DRIVEWAY, analysis, make_doorstep, make_runner

# Scenarios: ("set", datetime) | ("advance_h", hours) | ("event", id, type, analysis) | ("pickup",)
SCENARIOS = {
    "full story": [
        ("event", "pkg", "package", analysis(package=0.95)),
        ("advance_h", 1.3), ("event", "afternoon", "vehicle", analysis(vehicle=0.9, view=DRIVEWAY)),
        ("advance_h", 1.7), ("event", "still", "motion", analysis(package=0.9)),
        ("pickup",),
        ("set", datetime(2026, 10, 7, 3, 0)), ("event", "night", "vehicle", analysis(vehicle=0.9, view=DRIVEWAY)),
    ],
    "theft": [
        ("event", "pkg", "package", analysis(package=0.95)),
        ("advance_h", 1), ("event", "other-cam", "motion", analysis(view=DRIVEWAY)),
        ("advance_h", 1), ("event", "gone", "motion", analysis(package=0.0)),
    ],
    "no reference view": [
        ("event", "pkg", "package", analysis(package=0.95, view=None)),
        ("event", "gone", "motion", analysis(package=0.0)),
    ],
    "short capture and button press": [
        ("event", "pkg", "package", analysis(package=0.95)),
        ("event", "short", "motion", analysis(package=0.0, frames=2)),
        ("event", "bell", "button_press", None),
        ("advance_h", 4), ("event", "late", "human", analysis(person=0.8)),
    ],
    "package seen on other event types": [
        ("event", "veh", "vehicle", analysis(package=0.9, vehicle=0.9)),
        ("event", "weak", "package", analysis(package=0.1)),
        ("set", datetime(2026, 10, 7, 23, 30)), ("event", "late-motion", "motion", analysis()),
    ],
}


def play(steps, record):
    """Run a scenario; `record(ds, event_id, type, analysis)` handles events."""
    def go(ds):
        for step in steps:
            if step[0] == "set":
                ds.clock.set(step[1])
            elif step[0] == "advance_h":
                ds.clock.advance(step[1] * 3600)
            elif step[0] == "pickup":
                ds.mark_picked_up(ds.store.open_package().id)
            else:
                record(ds, *step[1:])
        return snapshot(ds)
    return go


def snapshot(ds):
    return {
        "packages": [(p.status.value, p.arrived_sim_ts[:16], (p.reminded_sim_ts or "")[:16],
                      (p.resolved_sim_ts or "")[:16], p.resolved_event_id) for p in ds.store.list_packages()],
        "notifications": [(n.audience, n.kind, n.text) for n in ds.store.list_notifications()],
        "events": [(e.event_id, e.package_check, e.unusual_score, e.package_seen) for e in ds.store.list_events()],
    }


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_rules_brain_matches_reference_rules(store, name):
    steps = SCENARIOS[name]
    reference = play(steps, lambda ds, eid, typ, an: ds.record_event(eid, typ, analysis=an))(
        make_doorstep(SQLiteStore(":memory:")))

    runner = make_runner(store, RulesBrain())
    agent = play(steps, lambda ds, eid, typ, an: asyncio.run(
        runner.handle_event(eid, typ, preloaded_analysis=an)))(runner.doorstep)
    assert agent == reference


def test_rules_brain_records_trace_reason_and_rules_source(store):
    runner = make_runner(store, RulesBrain())
    ctx = asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))
    assert [t["tool"] for t in ctx.trace] == ["start_live_capture", "describe_scene", "get_package_state",
                                               "update_package_state", "notify_resident"]
    assert ctx.reason.startswith("Capture shows package (20 frames, description: stub).")
    assert "New package recorded; resident told." in ctx.reason
    ev = runner.doorstep.store.get_event("pkg")
    assert ev.agent_brain == "rules" and ev.agent_reason == ctx.reason and len(ev.agent_trace) == 5
    [n] = runner.doorstep.store.list_notifications()
    assert n.source == "rules" and n.extra["observation_source"] == "stub"


def test_rules_brain_continues_when_capture_fails(store):
    async def no_frames(*a):
        return None
    runner = make_runner(store, RulesBrain(), capture=no_frames)
    ctx = asyncio.run(runner.handle_event("veh", "vehicle"))
    assert "No usable capture (the camera did not return any frames)." in ctx.reason
    assert [t["tool"] for t in ctx.trace][-1] == "get_visit_baseline"
    assert runner.doorstep.store.get_event("veh").frame_count == 0
