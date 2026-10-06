"""Run the DoorSight demo story end to end against real Ring sandbox captures.

    1. Package event at 2:10 PM (sim)  -> package present, resident notified
       Vehicle event at 3:30 PM while the package is open (another camera view)
                                       -> "different view — can't verify package", stays present
    2. Advance the demo clock to 3 h after arrival -> reminder to the resident
    3. Resident presses "I picked it up" -> picked_up
    4. Vehicle event at 03:00 next day -> unusual-hour alert to the caregiver

Uses a separate database (data/demo.db, emptied each run) so live state is untouched.
Pass --db data/doorsight.db to load the story into the live server's database instead
(it is emptied first) and see it in the web UI.
Frames and analyses come from earlier sandbox captures (data/frames, data/analysis);
missing analyses are computed with the step-3 pipeline (Bedrock, else the stub).

Usage:
    python scripts/demo_story.py [--package-capture DIR] [--vehicle-capture DIR]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.clock import DemoClock  # noqa: E402
from backend.config import PROJECT_ROOT  # noqa: E402
from backend.doorstep import Doorstep, fmt_time  # noqa: E402
from backend.store import SQLiteStore  # noqa: E402
from backend.vision.analyze import analysis_path, analyze_event  # noqa: E402

FRAMES = PROJECT_ROOT / "data" / "frames"
DEMO_DB = PROJECT_ROOT / "data" / "demo.db"


def load_analysis(capture_dir: Path, event_type: str) -> dict:
    path = analysis_path(capture_dir.name)
    if path.exists():
        return json.loads(path.read_text())
    print(f"  (no saved analysis for {capture_dir.name}; running step-3 analysis)")
    return analyze_event(capture_dir.name, event_type, capture_dir)


def step(title: str) -> None:
    print(f"\n=== {title}")


def show_outcome(out) -> None:
    ev = out.event
    print(f"  event   {ev.event_id}  type={ev.event_type}  sim={ev.sim_ts}  real={ev.real_ts}")
    print(f"  seen    package={ev.package_seen} vehicle={ev.vehicle_seen} person={ev.person_seen}  "
          f"frames={ev.frame_count} ({ev.frame_source})  description={ev.description_source}")
    if out.package_action:
        print(f"  package {out.package_action}: {out.package.id} -> {out.package.status.value}")
    if ev.package_check:
        vc = out.view_check or {}
        detail = f"  (view hash distance {vc['hash_distance']}, histogram correlation {vc['hist_correlation']})" \
            if vc.get("hash_distance") is not None else ""
        print(f"  check   {ev.package_check}{detail}")
    if out.unusual:
        print(f"  unusual score={out.unusual.score}  {out.unusual.explanation}")
    for n in out.notifications:
        print(f"  notify  [{n.audience}/{n.kind}] {n.text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-capture", type=Path, default=FRAMES / "sim-package-1791250581919")
    parser.add_argument("--vehicle-capture", type=Path, default=FRAMES / "sim-vehicle-1791250656310")
    parser.add_argument("--db", type=Path, default=DEMO_DB, help="SQLite file to write (emptied first)")
    args = parser.parse_args()

    store = SQLiteStore(args.db)
    store.clear()  # not unlink: a running server may hold the file open
    clock = DemoClock(tz="Asia/Kolkata")
    ds = Doorstep(store, clock, reminder_hours=3)
    today = clock.now().date()

    pkg_analysis = load_analysis(args.package_capture, "package")
    veh_analysis = load_analysis(args.vehicle_capture, "vehicle")

    step("1. Package event (sim 2:10 PM)")
    clock.set(datetime.combine(today, datetime.min.time()).replace(hour=14, minute=10))
    out = ds.record_event(f"demo-{args.package_capture.name}", "package", analysis=pkg_analysis, source="demo")
    show_outcome(out)
    package_id = out.package.id
    arrived = clock.now()

    step("1b. Vehicle event while the package is still open (sim 3:30 PM, a different camera view)")
    clock.advance(80 * 60)
    out = ds.record_event(f"demo-afternoon-{args.vehicle_capture.name}", "vehicle", analysis=veh_analysis,
                          source="demo")
    show_outcome(out)
    status = store.get_package(package_id).status.value
    print(f"  package {package_id} is still: {status}")
    assert status == "present", "a different camera view must not change the package state"

    step("2. Advance demo clock to 3 h after arrival")
    clock.advance((arrived + timedelta(hours=3) - clock.now()).total_seconds())
    print(f"  sim now {fmt_time(clock.now())}")
    for n in ds.check_reminders():
        print(f"  notify  [{n.audience}/{n.kind}] {n.text}")

    step('3. Resident presses "I picked up the package"')
    pkg = ds.mark_picked_up(package_id)
    print(f"  package {pkg.id} -> {pkg.status.value} at sim {pkg.resolved_sim_ts}")

    step("4. Vehicle event (sim 03:00 next day)")
    clock.set(datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=3))
    out = ds.record_event(f"demo-night-{args.vehicle_capture.name}", "vehicle", analysis=veh_analysis, source="demo")
    show_outcome(out)

    step("Final state")
    print(f"  demo clock: {clock.as_dict()['sim_now']} (offset {clock.offset_s / 3600:+.2f} h)")
    print("  packages:")
    for p in store.list_packages():
        print(f"    {p.id}  {p.status.value:<9}  arrived {p.arrived_sim_ts}  reminded {p.reminded_sim_ts}  "
              f"resolved {p.resolved_sim_ts}")
    print("  events:")
    for e in store.list_events():
        score = f"  unusual={e.unusual_score}" if e.unusual_score is not None else ""
        check = f"  [{e.package_check}]" if e.package_check else ""
        print(f"    {e.sim_ts}  {e.event_type:<8} {e.event_id}{score}{check}")
    print("  notifications:")
    for n in store.list_notifications():
        print(f"    {n.sim_ts}  {n.audience:<9} {n.kind:<16} source={n.source:<5} event={n.event_id}")
        print(textwrap.indent(textwrap.fill(n.text, 96), "      "))
    print(f"\n  database: {args.db}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
