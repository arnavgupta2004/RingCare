"""Daily digest facts and text, from stored state only."""

import asyncio
from datetime import datetime

from backend.agent import RulesBrain
from tests.agent_helpers import DRIVEWAY, analysis, make_runner


def story(store):
    runner = make_runner(store, RulesBrain())
    ds = runner.doorstep
    asyncio.run(runner.handle_event("pkg", "package", preloaded_analysis=analysis(package=0.95)))
    ds.clock.advance(80 * 60)
    asyncio.run(runner.handle_event("afternoon", "vehicle", preloaded_analysis=analysis(vehicle=0.9, view=DRIVEWAY)))
    ds.clock.set(datetime(2026, 10, 6, 17, 10))
    ds.check_reminders()
    ds.mark_picked_up(ds.store.open_package().id)
    ds.clock.set(datetime(2026, 10, 7, 3, 0))
    asyncio.run(runner.handle_event("night", "vehicle", preloaded_analysis=analysis(vehicle=0.9, view=DRIVEWAY)))
    return runner


def test_digest_facts_for_last_24_hours(store):
    ds = story(store).doorstep
    start, end = ds.digest_period()
    f = ds.digest_facts(start, end)
    assert f["start"].startswith("2026-10-06T03:00") and f["end"].startswith("2026-10-07T03:00")
    assert f["unusual"], "an event in the same second the digest runs must be included"
    assert [x[:16] for x in f["deliveries"]] == ["2026-10-06T14:10"]
    assert [x[:16] for x in f["pickups"]] == ["2026-10-06T17:10"]
    assert [x[:16] for x in f["reminders"]] == ["2026-10-06T17:10"]
    assert f["missing"] == [] and f["still_at_door"] == []
    assert [(u["type"], u["score"]) for u in f["unusual"]] == [("vehicle", 0.91)]
    assert f["visits"] == {"package": 1, "vehicle": 2}
    assert f["caregiver_alerts"] == 1 and f["observation_sources"] == ["stub"]


def test_digest_text_is_plain_and_complete(store):
    ds = story(store).doorstep
    text = ds.digest_text(ds.digest_facts(*ds.digest_period()), note="One evening delivery, collected.")
    assert text.splitlines()[0] == "DoorSight daily summary: Tue 3:00 AM to Wed 3:00 AM"
    for line in ["- Deliveries: 1 package(s) arrived (Tue 2:10 PM).",
                 "- Picked up by the resident: 1 (Tue 5:10 PM).",
                 "- Reminders sent to the resident: 1 (Tue 5:10 PM).",
                 "- Possible missing packages: none.",
                 "- Unusual-hour activity: 1 (vehicle at Wed 3:00 AM (score 0.91)).",
                 "- All door events: 1 package, 2 vehicle.",
                 "- Still at the door now: nothing.",
                 "Note: One evening delivery, collected."]:
        assert line in text, line
    assert "automatic estimates from the local detector" in text


def test_digest_for_a_calendar_day_and_an_empty_day(store):
    ds = story(store).doorstep
    f = ds.digest_facts(*ds.digest_period("2026-10-07"))
    assert f["deliveries"] == [] and [u["type"] for u in f["unusual"]] == ["vehicle"]
    empty = ds.digest_text(ds.digest_facts(*ds.digest_period("2026-10-01")))
    assert "- Deliveries: none." in empty and "- Unusual-hour activity: none." in empty
    assert "automatic estimates" not in empty


def test_digest_tool_sends_to_caregiver_with_facts(store):
    runner = story(store)
    ctx = asyncio.run(runner.write_digest())
    [d] = [n for n in runner.doorstep.store.list_notifications("caregiver") if n.kind == "daily_digest"]
    assert d.source == "rules" and d.event_id is None and d.status == "logged"
    assert d.extra["facts"]["visits"] == {"package": 1, "vehicle": 2}
    assert ctx.trace[0]["tool"] == "write_daily_digest" and ctx.trace[0]["ok"]
