from datetime import datetime, timezone

import pytest

from backend.clock import DemoClock
from backend.doorstep import Doorstep, PackageStateError, VisitProfile, score_unusual_hour
from backend.store import PackageStatus, SQLiteStore

REAL = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)
PROFILE = VisitProfile(quiet_start_hour=22, quiet_end_hour=6, quiet_hour_prior=0.2,
                       active_hour_prior=3.0, unusual_threshold=0.7)


def analysis(package=0.0, vehicle=0.0, person=0.0, frames=20, source="stub"):
    def g(frac):
        return {"max_count": int(frac > 0), "frames_present": [], "frame_fraction": frac, "best_confidence": 0.5}
    return {
        "frame_count": frames,
        "frame_source": "ring_whep",
        "detections": {"summary": {"package": g(package), "vehicle": g(vehicle), "person": g(person)}},
        "description": {"source": source, "result": {"accessible_description": "test"}},
    }


@pytest.fixture
def ds():
    clock = DemoClock(tz="Asia/Kolkata", real_now=lambda: REAL)
    clock.set(datetime(2026, 10, 6, 14, 10))
    return Doorstep(SQLiteStore(":memory:"), clock, reminder_hours=3, profile=PROFILE)


def arrive(ds, event_id="e-pkg"):
    return ds.record_event(event_id, "package", analysis=analysis(package=0.95), source="simulated")


# --- package lifecycle -------------------------------------------------------

def test_package_event_with_package_creates_present_and_notifies_resident(ds):
    out = arrive(ds)
    assert out.package_action == "created"
    assert out.package.status is PackageStatus.PRESENT
    [n] = ds.store.list_notifications()
    assert (n.audience, n.kind, n.event_id, n.source) == ("resident", "package_arrived", "e-pkg", "stub")
    assert n.text == "A package was left at your door at 2:10 PM."
    ev = ds.store.get_event("e-pkg")
    assert ev.sim_ts.startswith("2026-10-06T14:10") and ev.real_ts.startswith("2026-10-06T14:10")


def test_event_stores_both_real_and_sim_timestamps(ds):
    ds.clock.advance(3600 * 5)
    ev = arrive(ds).event
    assert ev.real_ts.startswith("2026-10-06T14:10")
    assert ev.sim_ts.startswith("2026-10-06T19:10") and ev.sim_hour == 19


def test_second_sighting_updates_open_package_without_duplicate(ds):
    arrive(ds)
    ds.clock.advance(1800)
    out = ds.record_event("e2", "motion", analysis=analysis(package=0.9))
    assert out.package_action == "still_present"
    assert len(ds.store.list_packages()) == 1
    assert ds.store.open_package().last_seen_sim_ts.startswith("2026-10-06T14:40")
    assert len(ds.store.list_notifications()) == 1


def test_package_seen_on_non_package_event_does_not_create(ds):
    out = ds.record_event("e-veh", "vehicle", analysis=analysis(package=0.9, vehicle=0.9))
    assert out.package_action is None and ds.store.list_packages() == []


def test_package_event_without_detection_creates_nothing(ds):
    out = ds.record_event("e-pkg", "package", analysis=analysis(package=0.1))
    assert out.package_action is None and ds.store.list_packages() == []


def test_reminder_only_after_reminder_hours(ds):
    pkg = arrive(ds).package
    ds.clock.advance(3 * 3600 - 60)
    assert ds.check_reminders() == []
    ds.clock.advance(60)
    [n] = ds.check_reminders()
    assert (n.audience, n.kind, n.package_id, n.source) == ("resident", "package_reminder", pkg.id, "rules")
    assert n.text == "Gentle reminder: the package left at your door at 2:10 PM is still there."
    stored = ds.store.get_package(pkg.id)
    assert stored.status is PackageStatus.REMINDED and stored.reminded_sim_ts.startswith("2026-10-06T17:10")


def test_reminder_is_sent_only_once(ds):
    arrive(ds)
    ds.clock.advance(4 * 3600)
    assert len(ds.check_reminders()) == 1
    ds.clock.advance(4 * 3600)
    assert ds.check_reminders() == []


def test_reminder_also_fires_from_next_event(ds):
    arrive(ds)
    ds.clock.advance(3 * 3600)
    out = ds.record_event("e-btn", "button_press")
    assert [n.kind for n in out.notifications] == ["package_reminder"]


@pytest.mark.parametrize("remind_first", [False, True])
def test_pickup_from_present_or_reminded(ds, remind_first):
    pkg = arrive(ds).package
    if remind_first:
        ds.clock.advance(3 * 3600)
        ds.check_reminders()
    picked = ds.mark_picked_up(pkg.id)
    assert picked.status is PackageStatus.PICKED_UP and picked.resolved_sim_ts
    assert ds.store.open_package() is None


def test_pickup_errors(ds):
    pkg = arrive(ds).package
    ds.mark_picked_up(pkg.id)
    with pytest.raises(PackageStateError):
        ds.mark_picked_up(pkg.id)
    with pytest.raises(LookupError):
        ds.mark_picked_up("pkg-nope")


def test_no_package_in_new_capture_marks_missing_and_alerts_caregiver(ds):
    pkg = arrive(ds).package
    ds.clock.advance(2 * 3600)
    out = ds.record_event("e-gone", "motion", analysis=analysis(package=0.0, frames=20))
    assert out.package_action == "missing"
    stored = ds.store.get_package(pkg.id)
    assert stored.status is PackageStatus.MISSING and stored.resolved_event_id == "e-gone"
    [alert] = ds.store.list_notifications("caregiver")
    assert alert.kind == "package_missing" and alert.event_id == "e-gone" and alert.package_id == pkg.id
    assert "2:10 PM" in alert.text and "4:10 PM" in alert.text and "not marked it as picked up" in alert.text


def test_reminded_package_can_go_missing(ds):
    pkg = arrive(ds).package
    ds.clock.advance(3 * 3600)
    ds.check_reminders()
    ds.record_event("e-gone", "motion", analysis=analysis(package=0.0))
    assert ds.store.get_package(pkg.id).status is PackageStatus.MISSING


def test_no_missing_after_pickup(ds):
    pkg = arrive(ds).package
    ds.mark_picked_up(pkg.id)
    out = ds.record_event("e-gone", "motion", analysis=analysis(package=0.0))
    assert out.package_action is None
    assert ds.store.get_package(pkg.id).status is PackageStatus.PICKED_UP
    assert ds.store.list_notifications("caregiver") == []


@pytest.mark.parametrize("frames,an", [(2, analysis(package=0.0, frames=2)), (0, None)])
def test_short_or_missing_capture_does_not_mark_missing(ds, frames, an):
    arrive(ds)
    out = ds.record_event("e-short", "motion", analysis=an)
    assert out.package_action is None
    assert ds.store.open_package().status is PackageStatus.PRESENT


# --- unusual-hour scoring ----------------------------------------------------

def at(hour):
    return datetime(2026, 10, 7, hour, 0)


def test_profile_quiet_hours_wrap_midnight():
    assert all(PROFILE.is_quiet(h) for h in (22, 23, 0, 3, 5))
    assert not any(PROFILE.is_quiet(h) for h in (6, 12, 21))


def test_quiet_hour_scores_high_with_explanation():
    s = score_unusual_hour("vehicle", at(3), [], PROFILE)
    assert s.score == pytest.approx(0.9, abs=0.01)
    assert s.explanation.startswith("Vehicle at 3:00 AM is unusual for this home")
    assert "quiet hours 22:00–06:00" in s.explanation


def test_active_hour_scores_zero():
    s = score_unusual_hour("motion", at(14), [], PROFILE)
    assert s.score == 0.0 and "normal time" in s.explanation


def test_observed_events_raise_the_baseline_for_that_hour():
    before = score_unusual_hour("vehicle", at(3), [], PROFILE).score
    after = score_unusual_hour("vehicle", at(3), [3, 3, 3], PROFILE)
    assert after.score < before and after.observed_at_hour == 3
    assert after.score < PROFILE.unusual_threshold


def test_score_is_clipped_to_unit_interval():
    s = score_unusual_hour("vehicle", at(3), [], VisitProfile(quiet_hour_prior=0.0, active_hour_prior=5.0))
    assert s.score == 1.0


def test_unusual_vehicle_queues_caregiver_alert_and_is_stored(ds):
    ds.clock.set(at(3))
    out = ds.record_event("e-night", "vehicle", analysis=analysis(vehicle=0.9))
    assert out.event.unusual_score >= 0.7
    [alert] = ds.store.list_notifications("caregiver")
    assert alert.kind == "unusual_hour" and alert.event_id == "e-night" and alert.source == "stub"
    assert alert.text.startswith("Unusual-hour activity at the front door. Vehicle at 3:00 AM is unusual")
    assert ds.store.get_event("e-night").unusual_explanation == out.unusual.explanation


def test_daytime_vehicle_no_alert_and_unscored_types_skip_scoring(ds):
    out = ds.record_event("e-day", "vehicle", analysis=analysis(vehicle=0.9))
    assert out.event.unusual_score == 0.0 and ds.store.list_notifications() == []
    ds.clock.set(at(3))
    btn = ds.record_event("e-btn", "button_press")
    assert btn.event.unusual_score is None and btn.unusual is None


def test_current_event_not_counted_in_its_own_baseline(ds):
    ds.clock.set(at(3))
    first = ds.record_event("n1", "vehicle", analysis=analysis(vehicle=0.9))
    second = ds.record_event("n2", "vehicle", analysis=analysis(vehicle=0.9))
    assert first.unusual.observed_at_hour == 0 and second.unusual.observed_at_hour == 1
