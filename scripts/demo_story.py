"""Run the DoorSight demo story end to end against real Ring sandbox captures.

    1. Package event at 2:10 PM (sim)  -> package present, resident notified
       Vehicle event at 3:30 PM while the package is open (another camera view)
                                       -> "different view — can't verify package", stays present
    2. Advance the demo clock to 3 h after arrival -> reminder to the resident
    3. Resident presses "I picked it up" -> picked_up
    4. Vehicle event at 03:00 next day -> unusual-hour alert to the caregiver
    5. Daily digest for the caregiver (last 24 h)

Every event goes through the agent (--brain rules|bedrock; default AGENT_BRAIN, else rules),
which records its tool calls and its reason on the event.

Uses a separate database (data/demo.db, emptied each run) so live state is untouched.
Pass --db data/doorsight.db to load the story into the live server's database instead
(it is emptied first) and see it in the web UI.
Frames and analyses come from earlier sandbox captures (data/frames, data/analysis);
missing analyses are computed with the step-3 pipeline (Bedrock, else the stub).

With --aws, state goes to DynamoDB (DYNAMODB_TABLE, emptied first), snapshots to S3
(S3_BUCKET) and caregiver alerts to SNS (SNS_TOPIC_ARN); run scripts/aws_setup.sh first.

Usage:
    python scripts/demo_story.py [--package-capture DIR] [--vehicle-capture DIR] [--db FILE | --aws]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import logging
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent import AgentRunner, RulesBrain, bedrock_brain  # noqa: E402
from backend.clock import DemoClock  # noqa: E402
from backend.config import PROJECT_ROOT  # noqa: E402
from backend.doorstep import Doorstep, fmt_time  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.notify import LogNotifier, SNSNotifier  # noqa: E402
from backend.snapshots import LocalSnapshots, S3Snapshots  # noqa: E402
from backend.store import SQLiteStore, StateStore  # noqa: E402
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


def show_outcome(ctx, store: StateStore) -> None:
    ev = store.get_event(ctx.event_id)
    print(f"  event   {ev.event_id}  type={ev.event_type}  sim={ev.sim_ts}  real={ev.real_ts}")
    print(f"  seen    package={ev.package_seen} vehicle={ev.vehicle_seen} person={ev.person_seen}  "
          f"frames={ev.frame_count} ({ev.frame_source})  description={ev.description_source}")
    tools = " -> ".join(t["tool"] + ("" if t.get("ok", True) else " ✗") for t in ctx.trace)
    print(f"  agent   [{ctx.brain}] {tools}")
    print(f"  reason  {ctx.reason}")
    if ctx.package_action or ev.package_check:
        vc = (ctx.assessment.view_check if ctx.assessment else None) or {}
        detail = f"  (view hash distance {vc['hash_distance']}, histogram correlation {vc['hist_correlation']})" \
            if vc.get("hash_distance") is not None else ""
        print(f"  package {ctx.package_action or 'unchanged'}: {ev.package_check}{detail}")
    if ctx.unusual:
        print(f"  unusual score={ctx.unusual.score}")
    for n in ctx.notifications:
        print(f"  notify  [{n.audience}/{n.kind} by {n.source}, {n.status}] {n.text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-capture", type=Path, default=FRAMES / "sim-package-1791250581919")
    parser.add_argument("--vehicle-capture", type=Path, default=FRAMES / "sim-vehicle-1791250656310")
    parser.add_argument("--db", type=Path, default=DEMO_DB, help="SQLite file to write (emptied first)")
    parser.add_argument("--aws", action="store_true", help="use DynamoDB + S3 + SNS from .env")
    parser.add_argument("--brain", choices=["rules", "bedrock"], default=None,
                        help="agent brain (default: AGENT_BRAIN if rules/bedrock, else rules)")
    parser.add_argument("--until", choices=["arrival", "reminder", "pickup", "night"], default="night",
                        help="stop after this step (e.g. 'reminder' leaves the package waiting, for the UI)")
    args = parser.parse_args()

    if args.aws:
        import os

        from backend.store.dynamodb import DynamoDBStore

        get_settings()  # load .env
        missing = [k for k in ("DYNAMODB_TABLE", "S3_BUCKET", "SNS_TOPIC_ARN") if not os.getenv(k)]
        if missing:
            sys.exit(f"--aws needs {', '.join(missing)} in .env; run scripts/aws_setup.sh first")
        store: StateStore = DynamoDBStore()
        snapshots = S3Snapshots(os.environ["S3_BUCKET"])
        notifier = SNSNotifier(os.environ["SNS_TOPIC_ARN"], fallback=LogNotifier(get_settings().logs_dir / "notifications.log"))
        where = f"DynamoDB table {store.table_name}, S3 bucket {snapshots.bucket}, SNS topic {notifier.topic_arn}"
    else:
        store = SQLiteStore(args.db)
        snapshots, notifier = LocalSnapshots(), LogNotifier()
        where = f"SQLite {args.db}, local snapshots, log-only alerts"
    store.clear()  # not unlink: a running server may hold the file open
    print(f"Backends: {where}")
    clock = DemoClock(tz="Asia/Kolkata")
    ds = Doorstep(store, clock, reminder_hours=3, snapshots=snapshots, notifier=notifier)
    get_settings()
    brain_name = args.brain or (os.getenv("AGENT_BRAIN") if os.getenv("AGENT_BRAIN") in ("rules", "bedrock") else "rules")
    brain = RulesBrain() if brain_name == "rules" else bedrock_brain()
    runner = AgentRunner(ds, brain, why=f"demo --brain {brain_name}")
    print(f"Agent brain: {brain.name}")
    today = clock.now().date()

    def handle(event_id: str, event_type: str, analysis: dict):
        ctx = asyncio.run(runner.handle_event(event_id, event_type, source="demo", preloaded_analysis=analysis))
        show_outcome(ctx, store)
        return ctx

    pkg_analysis = load_analysis(args.package_capture, "package")
    veh_analysis = load_analysis(args.vehicle_capture, "vehicle")

    step("1. Package event (sim 2:10 PM)")
    clock.set(datetime.combine(today, datetime.min.time()).replace(hour=14, minute=10))
    ctx = handle(f"demo-{args.package_capture.name}", "package", pkg_analysis)
    package_id = ctx.assessment.open_package.id
    arrived = clock.now()

    step("1b. Vehicle event while the package is still open (sim 3:30 PM, a different camera view)")
    clock.advance(80 * 60)
    handle(f"demo-afternoon-{args.vehicle_capture.name}", "vehicle", veh_analysis)
    status = store.get_package(package_id).status.value
    print(f"  package {package_id} is still: {status}")
    assert status == "present", "a different camera view must not change the package state"

    if args.until == "arrival":
        return finish(store, clock, where, snapshots)

    step("2. Advance demo clock to 3 h after arrival")
    clock.advance((arrived + timedelta(hours=3) - clock.now()).total_seconds())
    print(f"  sim now {fmt_time(clock.now())}")
    for n in ds.check_reminders():
        print(f"  notify  [{n.audience}/{n.kind}] {n.text}")

    if args.until == "reminder":
        return finish(store, clock, where, snapshots)

    step('3. Resident presses "I picked up the package"')
    pkg = ds.mark_picked_up(package_id)
    print(f"  package {pkg.id} -> {pkg.status.value} at sim {pkg.resolved_sim_ts}")

    if args.until == "pickup":
        return finish(store, clock, where, snapshots)

    step("4. Vehicle event (sim 03:00 next day)")
    clock.set(datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=3))
    handle(f"demo-night-{args.vehicle_capture.name}", "vehicle", veh_analysis)

    step("5. Daily digest for the caregiver (last 24 hours)")
    ctx = asyncio.run(runner.write_digest())
    digest = next(n for n in ctx.notifications if n.kind == "daily_digest")
    print(f"  agent   [{ctx.brain}] " + " -> ".join(t["tool"] for t in ctx.trace))
    print(f"  notify  [caregiver/daily_digest by {digest.source}, {digest.status}]")
    print(textwrap.indent(digest.text, "    | ", lambda _: True))

    return finish(store, clock, where, snapshots)


def finish(store: StateStore, clock: DemoClock, where: str, snapshots) -> int:
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
        print(f"      snapshot {e.snapshot}")
    print("  notifications:")
    for n in store.list_notifications():
        sent = f"  sns={n.extra['sns_message_id']}" if n.extra.get("sns_message_id") else ""
        print(f"    {n.sim_ts}  {n.audience:<9} {n.kind:<16} source={n.source:<5} status={n.status}{sent}")
        print(textwrap.indent(textwrap.fill(n.text, 96), "      "))
    latest = next((e for e in reversed(store.list_events()) if e.snapshot), None)
    if latest:
        url = snapshots.url(latest.snapshot) or ""
        print(f"\n  UI snapshot URL for the latest event: {url[:110]}{'…' if len(url) > 110 else ''}")
    print(f"  backends: {where}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
