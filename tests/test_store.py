"""StateStore contract, run against SQLite and DynamoDB (moto) via the `store` fixture."""

import pytest

from backend.store import EventRecord, Notification, Package, PackageStatus

VIEW = {"version": 1, "device_id": None,
        "frames": [{"frame": "f.jpg", "phash": "0f0f0f0f0f0f0f0f", "hist": [0.5, 0.25], "size": [1280, 720]}]}


def ev(event_id, sim_ts, **kw):
    return EventRecord(event_id=event_id, event_type=kw.pop("event_type", "vehicle"), real_ts="2026-10-06T08:00:00+05:30",
                       sim_ts=sim_ts, sim_hour=int(sim_ts[11:13]), **kw)


def pkg(id_, arrived, status=PackageStatus.PRESENT, view=None):
    return Package(id=id_, status=status, arrived_event_id="e1", arrived_sim_ts=arrived,
                   arrived_real_ts=arrived, last_seen_sim_ts=arrived, arrival_view=view)


def note(id_, sim_ts, audience="resident", extra=None):
    return Notification(id=id_, audience=audience, kind="package_arrived", text=f"text {id_}", event_id="e1",
                        source="stub", sim_ts=sim_ts, real_ts=sim_ts, extra=extra or {})


def test_event_round_trip_preserves_types_and_nones(store):
    e = ev("e1", "2026-10-06T14:10:00+05:30", frame_count=20, package_seen=True, unusual_score=0.91,
           unusual_explanation="x", description_source="stub")
    store.add_event(e)
    got = store.get_event("e1")
    assert got == e
    assert isinstance(got.sim_hour, int) and isinstance(got.frame_count, int) and isinstance(got.unusual_score, float)
    assert got.snapshot is None and got.package_check is None
    assert store.get_event("nope") is None


def test_events_listed_in_sim_time_order_and_filtered(store):
    store.add_event(ev("late", "2026-10-07T03:00:00+05:30"))
    store.add_event(ev("early", "2026-10-06T14:10:00+05:30", event_type="package"))
    store.add_event(ev("mid", "2026-10-06T15:30:00+05:30"))
    assert [e.event_id for e in store.list_events()] == ["early", "mid", "late"]
    assert [e.event_id for e in store.list_events(["vehicle"])] == ["mid", "late"]


def test_re_adding_event_with_new_time_replaces_it(store):
    store.add_event(ev("e1", "2026-10-06T14:10:00+05:30"))
    store.add_event(ev("e1", "2026-10-06T16:00:00+05:30"))
    assert [e.sim_ts for e in store.list_events()] == ["2026-10-06T16:00:00+05:30"]
    assert store.get_event("e1").sim_hour == 16


def test_package_round_trip_update_and_status_filter(store):
    store.create_package(pkg("p1", "2026-10-06T14:10:00+05:30", view=VIEW))
    store.create_package(pkg("p2", "2026-10-06T15:00:00+05:30"))
    p1 = store.get_package("p1")
    assert p1.arrival_view == VIEW and p1.status is PackageStatus.PRESENT
    p1.status, p1.reminded_sim_ts = PackageStatus.REMINDED, "2026-10-06T17:10:00+05:30"
    store.update_package(p1)
    assert store.get_package("p1").status is PackageStatus.REMINDED
    assert [p.id for p in store.list_packages()] == ["p1", "p2"]
    assert [p.id for p in store.list_packages([PackageStatus.REMINDED])] == ["p1"]
    assert store.open_package().id == "p2"  # newest open package
    assert store.get_package("nope") is None


def test_duplicate_package_create_is_rejected(store):
    store.create_package(pkg("p1", "2026-10-06T14:10:00+05:30"))
    with pytest.raises(Exception):
        store.create_package(pkg("p1", "2026-10-06T14:10:00+05:30"))


def test_notifications_keep_insert_order_within_same_time_and_filter(store):
    t = "2026-10-06T14:10:00+05:30"
    store.add_notification(note("n1", t, extra={"score": 0.91, "nested": {"a": [1, 2]}}))
    store.add_notification(note("n2", t, audience="caregiver"))
    store.add_notification(note("n0", "2026-10-06T09:00:00+05:30"))
    assert [n.id for n in store.list_notifications()] == ["n0", "n1", "n2"]
    assert [n.id for n in store.list_notifications("caregiver")] == ["n2"]
    assert store.list_notifications()[1].extra == {"score": 0.91, "nested": {"a": [1, 2]}}


def test_clear_removes_everything(store):
    store.add_event(ev("e1", "2026-10-06T14:10:00+05:30"))
    store.create_package(pkg("p1", "2026-10-06T14:10:00+05:30"))
    store.add_notification(note("n1", "2026-10-06T14:10:00+05:30"))
    store.clear()
    assert store.list_events() == [] and store.list_packages() == [] and store.list_notifications() == []
    assert store.get_event("e1") is None and store.get_package("p1") is None
    store.create_package(pkg("p1", "2026-10-06T14:10:00+05:30"))  # id is free again


def test_notification_without_event_id_round_trips(store):
    n = note("digest", "2026-10-06T21:00:00+05:30", audience="caregiver")
    n.event_id = None
    store.add_notification(n)
    assert store.list_notifications()[0].event_id is None
