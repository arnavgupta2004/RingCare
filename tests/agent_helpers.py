"""Shared fixtures-as-functions for agent tests."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.agent import AgentRunner
from backend.clock import DemoClock
from backend.doorstep import Doorstep, VisitProfile
from backend.notify import LogNotifier

REAL = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)
PROFILE = VisitProfile(quiet_start_hour=22, quiet_end_hour=6, quiet_hour_prior=0.2,
                       active_hour_prior=3.0, unusual_threshold=0.7)


def _view(phash, hist):
    return {"version": 1, "device_id": None,
            "frames": [{"frame": "frame_000.jpg", "phash": phash, "hist": hist, "size": [1280, 720]}]}


FRONT_DOOR = _view("0f0f0f0f0f0f0f0f", [float(i % 4 == 0) for i in range(32)])
DRIVEWAY = _view("70f0f0f0f0f0f0f0", [float(i % 4 == 2) for i in range(32)])


def analysis(package=0.0, vehicle=0.0, person=0.0, frames=20, source="stub", view=FRONT_DOOR):
    def g(frac):
        return {"max_count": int(frac > 0), "frames_present": [], "frame_fraction": frac, "best_confidence": 0.5}
    return {
        "frame_count": frames, "frame_source": "ring_whep", "view_fingerprint": view,
        "detections": {"summary": {"package": g(package), "vehicle": g(vehicle), "person": g(person)}},
        "description": {"source": source, "result": {"accessible_description": "test scene",
                                                     "scene_summary": "test summary"}},
    }


def make_doorstep(store) -> Doorstep:
    clock = DemoClock(tz="Asia/Kolkata", real_now=lambda: REAL)
    clock.set(datetime(2026, 10, 6, 14, 10))
    return Doorstep(store, clock, reminder_hours=3, profile=PROFILE, notifier=LogNotifier())


def make_runner(store, brain=None, **kw) -> AgentRunner:
    return AgentRunner(make_doorstep(store), brain, **kw)
