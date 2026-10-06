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

    # notifications

    def _notify(self, audience: str, kind: str, text: str, *, event_id: str | None, source: str,
                package_id: str | None = None, extra: dict[str, Any] | None = None) -> Notification:
        n = Notification(
            id=_new_id("ntf"), audience=audience, kind=kind, text=text, event_id=event_id,
            source=source, sim_ts=self.clock.now().isoformat(timespec="seconds"), real_ts=self.clock.real_now().isoformat(timespec="seconds"),
            package_id=package_id, extra=extra or {},
        )
        if audience == "caregiver":
            n.status = self.notifier.send(n)  # sent | logged | failed
        self.store.add_notification(n)
        logger.info("queued %s notification (%s): %s", audience, kind, text)
        return n

    # events

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
        now, real = self.clock.now(), self.clock.real_now()
        obs = observations(analysis)

        unusual = None
        if event_type in SCORED_EVENT_TYPES:
            history = [e.sim_hour for e in self.store.list_events(sorted(SCORED_EVENT_TYPES))]
            unusual = score_unusual_hour(event_type, now, history, self.profile)

        event = EventRecord(
            event_id=event_id, event_type=event_type, real_ts=real.isoformat(timespec="seconds"), sim_ts=now.isoformat(timespec="seconds"),
            sim_hour=now.hour, device_id=device_id, source=source, frame_source=obs["frame_source"],
            frame_count=obs["frame_count"], package_seen=obs["package"], vehicle_seen=obs["vehicle"],
            person_seen=obs["person"], description_source=obs["description_source"],
            accessible_description=obs["accessible_description"],
            unusual_score=unusual.score if unusual else None,
            unusual_explanation=unusual.explanation if unusual else None,
            snapshot=self.snapshots.publish(obs["snapshot"]),
        )
        outcome = EventOutcome(event=event, unusual=unusual, notifications=list(reminders))
        obs_source = obs["description_source"] or "rules"
        view = dict(obs["view"], device_id=device_id) if obs["view"] else None

        # package lifecycle
        open_pkg = self.store.open_package()
        check = None
        if open_pkg and obs["frame_count"] > 0:
            outcome.view_check = compare_views(open_pkg.arrival_view, view)
        same_view = bool(outcome.view_check and outcome.view_check["match"])
        cant_verify = CHECK_NO_REFERENCE if (outcome.view_check or {}).get("reason") == "no reference view" \
            else CHECK_DIFFERENT_VIEW

        if obs["package"] and not open_pkg and event_type == "package":
            pkg = Package(
                id=_new_id("pkg"), status=PackageStatus.PRESENT, arrived_event_id=event_id,
                arrived_sim_ts=now.isoformat(timespec="seconds"), arrived_real_ts=real.isoformat(timespec="seconds"),
                last_seen_sim_ts=now.isoformat(timespec="seconds"), arrival_view=view,
            )
            self.store.create_package(pkg)
            check = CHECK_NEW
            outcome.package_action, outcome.package = "created", pkg
            outcome.notifications.append(self._notify(
                "resident", "package_arrived", f"A package was left at your door at {fmt_time(now)}.",
                event_id=event_id, source=obs_source, package_id=pkg.id,
            ))
        elif open_pkg and obs["frame_count"] == 0:
            pass  # no capture: nothing to say about the package
        elif open_pkg and not same_view:
            check = cant_verify
            outcome.package_action, outcome.package = "view_mismatch", open_pkg
            logger.info("event %s: %s (package %s stays %s)", event_id, check, open_pkg.id, open_pkg.status.value)
        elif open_pkg and obs["package"]:
            open_pkg.last_seen_sim_ts = now.isoformat(timespec="seconds")
            self.store.update_package(open_pkg)
            check = CHECK_PRESENT
            outcome.package_action, outcome.package = "still_present", open_pkg
        elif open_pkg and obs["frame_count"] < MIN_FRAMES_FOR_ABSENCE:
            check = CHECK_TOO_SHORT
        elif open_pkg:
            open_pkg.status = PackageStatus.MISSING
            open_pkg.resolved_sim_ts = now.isoformat(timespec="seconds")
            open_pkg.resolved_event_id = event_id
            self.store.update_package(open_pkg)
            check = CHECK_GONE
            outcome.package_action, outcome.package = "missing", open_pkg
            arrived = datetime.fromisoformat(open_pkg.arrived_sim_ts)
            outcome.notifications.append(self._notify(
                "caregiver", "package_missing",
                f"Possible missing package: a package left at the door at {fmt_time(arrived)} is no longer "
                f"visible at {fmt_time(now)}, and the resident has not marked it as picked up. Please check in.",
                event_id=event_id, source=obs_source, package_id=open_pkg.id,
                extra={"arrived_sim_ts": open_pkg.arrived_sim_ts, "last_seen_sim_ts": open_pkg.last_seen_sim_ts,
                       "view_check": outcome.view_check},
            ))

        event.package_check = check
        self.store.add_event(event)

        # unusual hour
        if unusual and unusual.score >= self.profile.unusual_threshold:
            outcome.notifications.append(self._notify(
                "caregiver", "unusual_hour",
                f"Unusual-hour activity at the front door. {unusual.explanation}",
                event_id=event_id, source=obs_source, extra={"score": unusual.score},
            ))
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
                out.append(self._notify(
                    "resident", "package_reminder",
                    f"Gentle reminder: the package left at your door at {fmt_time(arrived)} is still there.",
                    event_id=pkg.arrived_event_id, source="rules", package_id=pkg.id,
                ))
        return out

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
