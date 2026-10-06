"""Doorstep state machine: package lifecycle, unusual-hour scoring and queued notifications.

Package lifecycle (one open package at a time):

    present --(REMINDER_HOURS on the demo clock)--> reminded
    present | reminded --(resident presses "I picked it up")--> picked_up
    present | reminded --(new capture of the SAME VIEW shows no package, no pickup recorded)--> missing

A capture from a different camera view can't confirm or deny the package: it is recorded as
"different view — can't verify package" and the package state is left unchanged.

All times come from the demo clock (backend.clock), so a demo can skip ahead.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.clock import DemoClock, get_clock
from backend.config import PROJECT_ROOT
from backend.notify import LogNotifier, SNSNotifier, notifier_from_env
from backend.snapshots import LocalSnapshots, S3Snapshots, get_snapshots
from backend.store import EventRecord, Notification, Package, PackageStatus, StateStore, get_store
from backend.vision.fingerprint import compare_views, view_fingerprint

logger = logging.getLogger("doorstep")

DEFAULT_REMINDER_HOURS = 3.0
# A class counts as seen only if detected in at least this share of frames (filters
# one-off false positives such as a mailbox post labelled "person").
MIN_PRESENCE_FRACTION = 0.3
# A capture with fewer frames is too short to conclude that a package is gone.
MIN_FRAMES_FOR_ABSENCE = 3

CHECK_NEW = "new package at the door"
CHECK_PRESENT = "package still present (same view)"
CHECK_GONE = "package gone from its arrival view"
CHECK_DIFFERENT_VIEW = "different view — can't verify package"
CHECK_NO_REFERENCE = "no reference view — can't verify package"
CHECK_TOO_SHORT = "capture too short — can't verify package"
SCORED_EVENT_TYPES = {"vehicle", "motion", "human", "other_motion"}
PROFILE_PATH = PROJECT_ROOT / "config" / "visit_profile.json"

DEFAULT_PROFILE = {
    "quiet_start_hour": 22,
    "quiet_end_hour": 6,
    "quiet_hour_prior": 0.2,
    "active_hour_prior": 3.0,
    "unusual_threshold": 0.7,
}


class PackageStateError(ValueError):
    pass


# --- Profile & scoring ------------------------------------------------------


@dataclass
class VisitProfile:
    quiet_start_hour: int = 22
    quiet_end_hour: int = 6
    quiet_hour_prior: float = 0.2
    active_hour_prior: float = 3.0
    unusual_threshold: float = 0.7

    @classmethod
    def load(cls, path: Path | None = None) -> "VisitProfile":
        path = Path(os.getenv("VISIT_PROFILE", path or PROFILE_PATH))
        data = dict(DEFAULT_PROFILE)
        if path.exists():
            data.update({k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")})
        return cls(**data)

    def is_quiet(self, hour: int) -> bool:
        s, e = self.quiet_start_hour, self.quiet_end_hour
        return (s <= hour or hour < e) if s > e else (s <= hour < e)

    def prior(self, hour: int) -> float:
        return self.quiet_hour_prior if self.is_quiet(hour) else self.active_hour_prior

    def quiet_label(self) -> str:
        return f"{self.quiet_start_hour:02d}:00–{self.quiet_end_hour:02d}:00"


@dataclass
class UnusualScore:
    score: float
    explanation: str
    hour: int
    expected: float
    average: float
    observed_at_hour: int


def score_unusual_hour(
    event_type: str, when: datetime, observed_hours: list[int], profile: VisitProfile
) -> UnusualScore:
    """0 = normal time for visits at this home, 1 = very unusual.

    Baseline weight per hour = profile prior + observed vehicle/motion events in that hour.
    score = 1 - weight(hour) / mean hourly weight, clipped to [0, 1].
    """
    hour = when.hour
    counts = [0] * 24
    for h in observed_hours:
        counts[h] += 1
    weights = [profile.prior(h) + counts[h] for h in range(24)]
    average = sum(weights) / 24
    expected = weights[hour]
    score = round(max(0.0, min(1.0, 1 - expected / average)) if average else 0.0, 2)

    what = "Vehicle" if event_type == "vehicle" else "Movement"
    at = fmt_time(when)
    seen = f"{counts[hour]} seen at this hour before" if counts[hour] else "none seen at this hour before"
    if score >= profile.unusual_threshold:
        explanation = (f"{what} at {at} is unusual for this home: about {expected:.1f} visits are expected "
                       f"in this hour versus {average:.1f} in an average hour ({seen}; quiet hours "
                       f"{profile.quiet_label()}).")
    elif score > 0:
        explanation = (f"{what} at {at} is somewhat less common than usual ({expected:.1f} expected "
                       f"versus {average:.1f} on average; {seen}).")
    else:
        explanation = f"{what} at {at} is a normal time for visits here ({expected:.1f} expected versus {average:.1f} on average)."
    return UnusualScore(score, explanation, hour, round(expected, 2), round(average, 2), counts[hour])


# --- Helpers ----------------------------------------------------------------


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def observations(analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Extract what we need from a step-3 analysis record."""
    if not analysis:
        return {"frame_count": 0, "package": False, "vehicle": False, "person": False,
                "description_source": None, "accessible_description": None, "frame_source": None,
                "view": None, "snapshot": []}
    summary = (analysis.get("detections") or {}).get("summary", {})
    desc = analysis.get("description") or {}

    def seen(group: str) -> bool:
        return summary.get(group, {}).get("frame_fraction", 0.0) >= MIN_PRESENCE_FRACTION

    return {
        "frame_count": analysis.get("frame_count", 0),
        "package": seen("package"),
        "vehicle": seen("vehicle"),
        "person": seen("person"),
        "description_source": desc.get("source"),
        "accessible_description": (desc.get("result") or {}).get("accessible_description"),
        "frame_source": analysis.get("frame_source"),
        "view": analysis.get("view_fingerprint") or _fingerprint_from_frames(analysis),
        "snapshot": _snapshot(analysis),
    }


def _snapshot(analysis: dict[str, Any]) -> list[str]:
    """Representative frames, relative to the data dir: the frame with the most relevant
    detections (else the middle one) first, then the first and last frames."""
    frames_dir = analysis.get("frames_dir")
    det_frames = (analysis.get("detections") or {}).get("frames") or []
    if not frames_dir or not det_frames:
        return []
    mid = len(det_frames) // 2
    best = max(det_frames, key=lambda f: (sum(f["counts"].values()), -abs(f["index"] - mid)))
    picks = [best["frame"]] + [f["frame"] for f in (det_frames[0], det_frames[-1]) if f["frame"] != best["frame"]]
    base = Path(frames_dir)
    base = base if base.is_absolute() else PROJECT_ROOT / base
    try:
        rel = base.relative_to(PROJECT_ROOT / "data")
    except ValueError:
        return []
    return [str(rel / name) for name in dict.fromkeys(picks)]
    best = max(det_frames, key=lambda f: (sum(f["counts"].values()), -abs(f["index"] - len(det_frames) // 2)))
    path = Path(frames_dir) / best["frame"]
    data_dir = PROJECT_ROOT / "data"
    try:
        return str((path if path.is_absolute() else PROJECT_ROOT / path).relative_to(data_dir))
    except ValueError:
        return None


def _fingerprint_from_frames(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """For analyses saved before fingerprints existed: compute from the capture's frames."""
    frames_dir = analysis.get("frames_dir")
    if not frames_dir:
        return None
    path = Path(frames_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return view_fingerprint(sorted(path.glob("frame_*.jpg")))


@dataclass
class EventOutcome:
    event: EventRecord
    package_action: str | None = None  # created | still_present | missing | view_mismatch | None
    view_check: dict[str, Any] | None = None
    package: Package | None = None
    unusual: UnusualScore | None = None
    notifications: list[Notification] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "package_action": self.package_action,
            "package": self.package.to_dict() if self.package else None,
            "unusual": self.unusual.__dict__ if self.unusual else None,
            "view_check": self.view_check,
            "notifications": [n.to_dict() for n in self.notifications],
        }


# --- Package assessment (the guard rails every brain goes through) -------------

PACKAGE_ACTIONS = ("create", "mark_seen", "mark_missing", "no_change")


@dataclass
class PackageAssessment:
    """What a capture can and cannot say about the package at the door.

    `allowed` is enforced by Doorstep.apply_package_action, so no brain (rules or model)
    can, e.g., mark a package missing from a different camera view.
    """

    open_package: Package | None
    view_check: dict[str, Any] | None
    recommended: str  # one of PACKAGE_ACTIONS
    allowed: list[str]
    check: str | None  # human-readable result, stored on the event
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        pkg = self.open_package
        return {
            "open_package": None if pkg is None else {
                "id": pkg.id, "status": pkg.status.value, "arrived_sim_ts": pkg.arrived_sim_ts,
                "last_seen_sim_ts": pkg.last_seen_sim_ts,
            },
            "view_check": self.view_check,
            "recommended_action": self.recommended,
            "allowed_actions": self.allowed,
            "check": self.check,
            "explanation": self.explanation,
        }


# --- Message templates (shared by the rules path and the rules brain) ------------

def arrival_text(now: datetime) -> str:
    return f"A package was left at your door at {fmt_time(now)}."


def reminder_text(arrived: datetime) -> str:
    return f"Gentle reminder: the package left at your door at {fmt_time(arrived)} is still there."


def missing_text(pkg: Package, now: datetime) -> str:
    arrived = datetime.fromisoformat(pkg.arrived_sim_ts)
    return (f"Possible missing package: a package left at the door at {fmt_time(arrived)} is no longer "
            f"visible at {fmt_time(now)}, and the resident has not marked it as picked up. Please check in.")


def unusual_text(unusual: UnusualScore) -> str:
    return f"Unusual-hour activity at the front door. {unusual.explanation}"


# --- The state machine -------------------------------------------------------


class Doorstep:
    def __init__(
        self,
        store: StateStore,
        clock: DemoClock,
        reminder_hours: float | None = None,
        profile: VisitProfile | None = None,
        snapshots: LocalSnapshots | S3Snapshots | None = None,
        notifier: LogNotifier | SNSNotifier | None = None,
    ):
        self.store = store
        self.clock = clock
        self.snapshots = snapshots or LocalSnapshots()
        self.notifier = notifier or LogNotifier()
        self.reminder_hours = float(reminder_hours if reminder_hours is not None
                                    else os.getenv("REMINDER_HOURS", DEFAULT_REMINDER_HOURS))
        self.profile = profile or VisitProfile.load()

    def _ts(self, dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    # notifications

    def notify(self, audience: str, kind: str, text: str, *, event_id: str | None, source: str,
               package_id: str | None = None, extra: dict[str, Any] | None = None) -> Notification:
        """Store a notification; caregiver notifications are also delivered (SNS or log)."""
        if audience not in ("resident", "caregiver"):
            raise ValueError(f"unknown audience {audience!r}")
        n = Notification(
            id=_new_id("ntf"), audience=audience, kind=kind, text=text, event_id=event_id,
            source=source, sim_ts=self._ts(self.clock.now()), real_ts=self._ts(self.clock.real_now()),
            package_id=package_id, extra=extra or {},
        )
        if audience == "caregiver":
            n.status = self.notifier.send(n)  # sent | logged | failed
        self.store.add_notification(n)
        logger.info("queued %s notification (%s): %s", audience, kind, text)
        return n

    _notify = notify  # backwards-compatible name

    # primitives used by record_event and by the agent's tools

    def score_event(self, event_type: str) -> UnusualScore | None:
        """Unusual-hour score for vehicle/motion events, against history before this event."""
        if event_type not in SCORED_EVENT_TYPES:
            return None
        history = [e.sim_hour for e in self.store.list_events(sorted(SCORED_EVENT_TYPES))]
        return score_unusual_hour(event_type, self.clock.now(), history, self.profile)

    def new_event(self, event_id: str, event_type: str, *, device_id: str | None, source: str,
                  obs: dict[str, Any], unusual: UnusualScore | None) -> EventRecord:
        now, real = self.clock.now(), self.clock.real_now()
        return EventRecord(
            event_id=event_id, event_type=event_type, real_ts=self._ts(real), sim_ts=self._ts(now),
            sim_hour=now.hour, device_id=device_id, source=source, frame_source=obs["frame_source"],
            frame_count=obs["frame_count"], package_seen=obs["package"], vehicle_seen=obs["vehicle"],
            person_seen=obs["person"], description_source=obs["description_source"],
            accessible_description=obs["accessible_description"],
            unusual_score=unusual.score if unusual else None,
            unusual_explanation=unusual.explanation if unusual else None,
            snapshot=self.snapshots.publish(obs["snapshot"]),
        )

    def save_event(self, event: EventRecord) -> None:
        self.store.add_event(event)

    def assess_package(self, event_type: str, obs: dict[str, Any], device_id: str | None) -> PackageAssessment:
        open_pkg = self.store.open_package()
        view = dict(obs["view"], device_id=device_id) if obs.get("view") else None
        view_check = compare_views(open_pkg.arrival_view, view) if open_pkg and obs["frame_count"] > 0 else None
        same_view = bool(view_check and view_check["match"])

        def result(recommended, allowed, check, explanation):
            return PackageAssessment(open_pkg, view_check, recommended, allowed, check, explanation)

        if obs["package"] and not open_pkg and event_type == "package":
            return result("create", ["create", "no_change"], CHECK_NEW,
                          "A package is visible and no package is being tracked.")
        if not open_pkg:
            why = ("A package is visible, but only package events start tracking a new package."
                   if obs["package"] else "No package is being tracked and none is visible.")
            return result("no_change", ["no_change"], None, why)
        if obs["frame_count"] == 0:
            return result("no_change", ["no_change"], None, "There is no capture to check the package against.")
        if not same_view:
            no_ref = (view_check or {}).get("reason") == "no reference view"
            check = CHECK_NO_REFERENCE if no_ref else CHECK_DIFFERENT_VIEW
            return result("no_change", ["no_change"], check,
                          "This capture is not from the camera view where the package arrived, "
                          "so it cannot confirm or deny the package.")
        if obs["package"]:
            return result("mark_seen", ["mark_seen", "no_change"], CHECK_PRESENT,
                          "The package is still visible in its arrival view.")
        if obs["frame_count"] < MIN_FRAMES_FOR_ABSENCE:
            return result("no_change", ["no_change"], CHECK_TOO_SHORT,
                          f"The capture has fewer than {MIN_FRAMES_FOR_ABSENCE} frames, too short to conclude "
                          "the package is gone.")
        return result("mark_missing", ["mark_missing", "no_change"], CHECK_GONE,
                      "The arrival view no longer shows the package and the resident has not marked a pickup.")

    def apply_package_action(self, action: str, assessment: PackageAssessment, event: EventRecord,
                             obs: dict[str, Any]) -> Package | None:
        """Apply a package action if the assessment allows it; raises PackageStateError otherwise."""
        if action not in PACKAGE_ACTIONS:
            raise PackageStateError(f"unknown action {action!r}; use one of {', '.join(PACKAGE_ACTIONS)}")
        if action not in assessment.allowed:
            raise PackageStateError(
                f"action {action!r} is not allowed here ({assessment.explanation}) "
                f"Allowed: {', '.join(assessment.allowed)}.")
        now = self._ts(self.clock.now())
        pkg = assessment.open_package
        if action == "create":
            view = dict(obs["view"], device_id=event.device_id) if obs.get("view") else None
            pkg = Package(id=_new_id("pkg"), status=PackageStatus.PRESENT, arrived_event_id=event.event_id,
                          arrived_sim_ts=now, arrived_real_ts=self._ts(self.clock.real_now()),
                          last_seen_sim_ts=now, arrival_view=view)
            self.store.create_package(pkg)
        elif action == "mark_seen":
            pkg.last_seen_sim_ts = now
            self.store.update_package(pkg)
        elif action == "mark_missing":
            pkg.status, pkg.resolved_sim_ts, pkg.resolved_event_id = PackageStatus.MISSING, now, event.event_id
            self.store.update_package(pkg)
        return pkg

    # events (reference rules path; the agent's rules brain makes the same decisions via tools)

    def record_event(
        self,
        event_id: str,
        event_type: str,
        *,
        analysis: dict[str, Any] | None = None,
        device_id: str | None = None,
        source: str = "webhook",
    ) -> EventOutcome:
        """Store an event and apply package lifecycle + unusual-hour rules."""
        reminders = self.check_reminders()  # bring time-based state up to date first
        now = self.clock.now()
        obs = observations(analysis)
        unusual = self.score_event(event_type)
        event = self.new_event(event_id, event_type, device_id=device_id, source=source, obs=obs, unusual=unusual)
        outcome = EventOutcome(event=event, unusual=unusual, notifications=list(reminders))
        obs_source = obs["description_source"] or "rules"

        assessment = self.assess_package(event_type, obs, device_id)
        outcome.view_check = assessment.view_check
        event.package_check = assessment.check
        action = assessment.recommended
        pkg = self.apply_package_action(action, assessment, event, obs)
        outcome.package = pkg
        outcome.package_action = {
            "create": "created", "mark_seen": "still_present", "mark_missing": "missing",
        }.get(action, "view_mismatch" if assessment.check in (CHECK_DIFFERENT_VIEW, CHECK_NO_REFERENCE) else None)
        if outcome.package_action is None:
            outcome.package = None
        if action == "create":
            outcome.notifications.append(self.notify(
                "resident", "package_arrived", arrival_text(now),
                event_id=event_id, source=obs_source, package_id=pkg.id))
        elif action == "mark_missing":
            outcome.notifications.append(self.notify(
                "caregiver", "package_missing", missing_text(pkg, now),
                event_id=event_id, source=obs_source, package_id=pkg.id,
                extra={"arrived_sim_ts": pkg.arrived_sim_ts, "last_seen_sim_ts": pkg.last_seen_sim_ts,
                       "view_check": assessment.view_check}))
        elif outcome.package_action == "view_mismatch":
            logger.info("event %s: %s (package %s stays %s)", event_id, assessment.check, pkg.id, pkg.status.value)
        self.save_event(event)

        if unusual and unusual.score >= self.profile.unusual_threshold:
            outcome.notifications.append(self.notify(
                "caregiver", "unusual_hour", unusual_text(unusual),
                event_id=event_id, source=obs_source, extra={"score": unusual.score}))
        return outcome

    def check_reminders(self) -> list[Notification]:
        """present packages older than REMINDER_HOURS (demo clock) -> reminded + resident reminder."""
        now = self.clock.now()
        out = []
        for pkg in self.store.list_packages([PackageStatus.PRESENT]):
            arrived = datetime.fromisoformat(pkg.arrived_sim_ts)
            if now - arrived >= timedelta(hours=self.reminder_hours):
                pkg.status, pkg.reminded_sim_ts = PackageStatus.REMINDED, now.isoformat(timespec="seconds")
                self.store.update_package(pkg)
                out.append(self.notify(
                    "resident", "package_reminder", reminder_text(arrived),
                    event_id=pkg.arrived_event_id, source="rules", package_id=pkg.id,
                ))
        return out

    # daily digest

    def digest_period(self, day: str | None = None) -> tuple[datetime, datetime]:
        """`day` = "YYYY-MM-DD" (home-local calendar day); default = the 24 hours ending now (sim).

        Both ends are inclusive, so an event recorded in the same second the digest runs is counted.
        """
        if day:
            start = datetime.fromisoformat(day).replace(tzinfo=self.clock.tz)
            return start, start + timedelta(days=1) - timedelta(seconds=1)
        end = self.clock.now().replace(microsecond=0)
        return end - timedelta(days=1), end

    def digest_facts(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Everything the caregiver digest says, computed from stored state only."""
        def within(ts: str | None) -> bool:
            return bool(ts) and start <= datetime.fromisoformat(ts) <= end

        events = [e for e in self.store.list_events() if within(e.sim_ts)]
        packages = self.store.list_packages()
        notes = [n for n in self.store.list_notifications() if within(n.sim_ts)]
        threshold = self.profile.unusual_threshold
        unusual = [e for e in events if e.unusual_score is not None and e.unusual_score >= threshold]
        sources = sorted({e.description_source for e in events if e.description_source})
        return {
            "start": self._ts(start), "end": self._ts(end),
            "deliveries": [p.arrived_sim_ts for p in packages if within(p.arrived_sim_ts)],
            "pickups": [p.resolved_sim_ts for p in packages
                        if p.status is PackageStatus.PICKED_UP and within(p.resolved_sim_ts)],
            "missing": [p.resolved_sim_ts for p in packages
                        if p.status is PackageStatus.MISSING and within(p.resolved_sim_ts)],
            "reminders": [n.sim_ts for n in notes if n.kind == "package_reminder"],
            "unusual": [{"sim_ts": e.sim_ts, "type": e.event_type, "score": e.unusual_score} for e in unusual],
            "visits": {t: sum(1 for e in events if e.event_type == t) for t in sorted({e.event_type for e in events})},
            "still_at_door": [p.arrived_sim_ts for p in packages if p.status.is_open],
            "caregiver_alerts": sum(1 for n in notes if n.audience == "caregiver" and n.kind != "daily_digest"),
            "observation_sources": sources,
        }

    def digest_text(self, facts: dict[str, Any], note: str | None = None) -> str:
        tz = self.clock.tz

        def t(ts: str) -> str:
            return datetime.fromisoformat(ts).astimezone(tz).strftime("%a %I:%M %p").replace(" 0", " ")

        def times(items: list[str]) -> str:
            return ", ".join(t(x) for x in items)

        lines = [f"DoorSight daily summary: {t(facts['start'])} to {t(facts['end'])}", ""]
        d = facts["deliveries"]
        lines.append(f"- Deliveries: {len(d)} package(s) arrived ({times(d)})." if d else "- Deliveries: none.")
        p = facts["pickups"]
        lines.append(f"- Picked up by the resident: {len(p)} ({times(p)})." if p else "- Picked up by the resident: none.")
        if facts["reminders"]:
            lines.append(f"- Reminders sent to the resident: {len(facts['reminders'])} ({times(facts['reminders'])}).")
        m = facts["missing"]
        lines.append(f"- Possible missing packages: {len(m)} ({times(m)})." if m else "- Possible missing packages: none.")
        u = facts["unusual"]
        if u:
            items = "; ".join(f"{x['type']} at {t(x['sim_ts'])} (score {x['score']:.2f})" for x in u)
            lines.append(f"- Unusual-hour activity: {len(u)} ({items}).")
        else:
            lines.append("- Unusual-hour activity: none.")
        if facts["visits"]:
            lines.append("- All door events: " + ", ".join(f"{n} {k}" for k, n in facts["visits"].items()) + ".")
        s = facts["still_at_door"]
        lines.append(f"- Still at the door now: {len(s)} package(s), arrived {times(s)}." if s
                     else "- Still at the door now: nothing.")
        if note:
            lines += ["", f"Note: {note.strip()}"]
        if "stub" in facts["observation_sources"]:
            lines += ["", "Scene descriptions were automatic estimates from the local detector "
                          "(the vision model was unavailable)."]
        return "\n".join(lines)

    def mark_picked_up(self, package_id: str) -> Package:
        pkg = self.store.get_package(package_id)
        if pkg is None:
            raise LookupError(f"package {package_id} not found")
        if not pkg.status.is_open:
            raise PackageStateError(f"package {package_id} is already {pkg.status.value}")
        pkg.status, pkg.resolved_sim_ts = PackageStatus.PICKED_UP, self.clock.now().isoformat(timespec="seconds")
        self.store.update_package(pkg)
        logger.info("package %s picked up", package_id)
        return pkg


_doorstep: Doorstep | None = None


def get_doorstep() -> Doorstep:
    global _doorstep
    if _doorstep is None:
        _doorstep = Doorstep(get_store(), get_clock(), snapshots=get_snapshots(), notifier=notifier_from_env())
    return _doorstep
