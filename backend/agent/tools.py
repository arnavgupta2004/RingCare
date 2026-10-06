"""The agent's tools, bound to one event (or one digest request).

Both brains go through these: the Bedrock brain via Strands tool calls, the rules brain by
calling the same methods directly. Every call is recorded in `ctx.trace`.

Tools are idempotent within an event (capture/describe are cached, a notification of the
same audience+kind is not sent twice), so a fallback from the Bedrock brain to the rules
brain part-way through an event can safely re-run the steps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strands import tool

from backend.doorstep import (
    PACKAGE_ACTIONS,
    Doorstep,
    PackageAssessment,
    PackageStateError,
    UnusualScore,
    observations,
)
from backend.store import EventRecord, Notification

logger = logging.getLogger("agent.tools")

CAPTURE_EVENT_TYPES = {"package", "vehicle", "motion", "human", "other_motion", "button_press"}
RESIDENT_KINDS = ("package_arrived", "info")
CAREGIVER_KINDS = ("package_missing", "unusual_hour", "info")
MAX_TEXT = 600
TRACE_OUTPUT_CHARS = 500

# capture(event_id, event_type, device_id) -> frames directory or None
CaptureFn = Callable[[str, str, str | None], Awaitable[Path | None]]
# analyze(event_id, event_type, frames_dir) -> step-3 analysis record
AnalyzeFn = Callable[[str, str, Path], dict[str, Any]]


class ToolError(RuntimeError):
    """A refusal or failure reported back to the brain as a tool error."""


@dataclass
class EventContext:
    event_id: str
    event_type: str
    device_id: str | None = None
    source: str = "webhook"
    preloaded_analysis: dict[str, Any] | None = None  # existing capture (demo / replay)
    brain: str = "rules"
    # filled in as tools run
    frames_dir: Path | None = None
    capture_note: str | None = None
    analysis: dict[str, Any] | None = None
    obs: dict[str, Any] | None = None
    unusual: UnusualScore | None = None
    unusual_checked: bool = False
    assessment: PackageAssessment | None = None
    package_action: str | None = None  # created | still_present | missing | None
    event: EventRecord | None = None
    notifications: list[Notification] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= TRACE_OUTPUT_CHARS else text[:TRACE_OUTPUT_CHARS] + "…"


class DoorstepTools:
    def __init__(self, doorstep: Doorstep, ctx: EventContext, *,
                 capture: CaptureFn | None = None, analyze: AnalyzeFn | None = None):
        self.ds = doorstep
        self.ctx = ctx
        self._capture = capture
        self._analyze = analyze

    def all(self) -> list:
        return [self.start_live_capture, self.describe_scene, self.get_package_state, self.update_package_state,
                self.get_visit_baseline, self.notify_resident, self.notify_caregiver, self.write_daily_digest]

    # --- trace -------------------------------------------------------------------

    def _record(self, name: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        entry = {"tool": name, "input": args, "brain": self.ctx.brain,
                 "sim_ts": self.ds.clock.now().isoformat(timespec="seconds")}
        started = time.perf_counter()
        try:
            result = fn()
            entry.update(ok=True, output=_short(result))
            return result
        except Exception as exc:
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            entry["ms"] = round((time.perf_counter() - started) * 1000)
            self.ctx.trace.append(entry)

    async def _record_async(self, name: str, args: dict[str, Any], coro_fn: Callable[[], Awaitable[Any]]) -> Any:
        entry = {"tool": name, "input": args, "brain": self.ctx.brain,
                 "sim_ts": self.ds.clock.now().isoformat(timespec="seconds")}
        started = time.perf_counter()
        try:
            result = await coro_fn()
            entry.update(ok=True, output=_short(result))
            return result
        except Exception as exc:
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            entry["ms"] = round((time.perf_counter() - started) * 1000)
            self.ctx.trace.append(entry)

    # --- helpers -----------------------------------------------------------------

    def ensure_event(self) -> EventRecord:
        """Create the event record once observations are known (snapshot upload happens here)."""
        ctx = self.ctx
        if ctx.event is None:
            if ctx.unusual is None:
                ctx.unusual = self.ds.score_event(ctx.event_type)
            ctx.event = self.ds.new_event(ctx.event_id, ctx.event_type, device_id=ctx.device_id,
                                          source=ctx.source, obs=ctx.obs or observations(None), unusual=ctx.unusual)
        return ctx.event

    def _observation_source(self) -> str | None:
        return (self.ctx.obs or {}).get("description_source")

    def _send(self, audience: str, kind: str, text: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ToolError("text is empty")
        if len(text) > MAX_TEXT:
            raise ToolError(f"text is too long ({len(text)} characters, max {MAX_TEXT})")
        existing = next((n for n in self.ctx.notifications if n.audience == audience and n.kind == kind), None)
        if existing:
            return {"status": "already_sent", "notification_id": existing.id}
        event = self.ensure_event()
        pkg = self.ctx.assessment.open_package if self.ctx.assessment else None
        n = self.ds.notify(audience, kind, text, event_id=event.event_id, source=self.ctx.brain,
                           package_id=pkg.id if pkg and kind.startswith("package") else None,
                           extra={"observation_source": self._observation_source(), **(extra or {})})
        self.ctx.notifications.append(n)
        return {"status": n.status, "notification_id": n.id}

    # --- tools -------------------------------------------------------------------

    @tool
    async def start_live_capture(self) -> dict:
        """Start a short live video session on the doorbell and save about one frame per second.

        Call this first for package, vehicle and motion events. Returns how many frames were
        captured. If frames from an existing capture of this event are available, they are used.
        """
        async def run():
            ctx = self.ctx
            if ctx.frames_dir is not None or ctx.preloaded_analysis is not None:
                if ctx.preloaded_analysis is not None and ctx.capture_note is None:
                    ctx.capture_note = "existing capture"
                count = (ctx.preloaded_analysis or {}).get("frame_count") \
                    or len(list(ctx.frames_dir.glob("frame_*.jpg")) if ctx.frames_dir else [])
                return {"frames": count, "source": ctx.capture_note or "already captured"}
            if ctx.event_type not in CAPTURE_EVENT_TYPES:
                raise ToolError(f"'{ctx.event_type}' events have no video to capture")
            if self._capture is None:
                raise ToolError("live capture is not available in this environment")
            frames_dir = await self._capture(ctx.event_id, ctx.event_type, ctx.device_id)
            if frames_dir is None:
                raise ToolError("the camera did not return any frames")
            ctx.frames_dir, ctx.capture_note = frames_dir, "live Ring WHEP session"
            return {"frames": len(list(frames_dir.glob("frame_*.jpg"))), "source": ctx.capture_note}

        return await self._record_async("start_live_capture", {}, run)

    @tool
    async def describe_scene(self) -> dict:
        """Describe what the captured frames show: objects detected and a plain-language description.

        Returns which of package / vehicle / person were seen (and in what share of frames),
        the description for the resident, and its source: "bedrock" (vision model) or "stub"
        (automatic estimate from the local detector, weaker evidence).
        """
        async def run():
            ctx = self.ctx
            if ctx.analysis is None:
                if ctx.preloaded_analysis is not None:
                    ctx.analysis = ctx.preloaded_analysis
                elif ctx.frames_dir is not None and self._analyze is not None:
                    ctx.analysis = await asyncio.to_thread(self._analyze, ctx.event_id, ctx.event_type, ctx.frames_dir)
                else:
                    raise ToolError("no frames to describe; call start_live_capture first")
                ctx.obs = observations(ctx.analysis)
            summary = (ctx.analysis.get("detections") or {}).get("summary", {})
            desc = ctx.analysis.get("description") or {}
            result = desc.get("result") or {}
            return {
                "frames": ctx.obs["frame_count"],
                "seen": {g: ctx.obs[g] for g in ("package", "vehicle", "person")},
                "share_of_frames": {g: v.get("frame_fraction", 0.0) for g, v in summary.items()},
                "description": result.get("accessible_description"),
                "scene_summary": result.get("scene_summary"),
                "description_source": desc.get("source"),
            }

        return await self._record_async("describe_scene", {}, run)

    @tool
    def get_package_state(self) -> dict:
        """Check the package being tracked at the door against what this capture shows.

        Returns the open package (if any), whether this capture is from the same camera view the
        package arrived in, the recommended action, and the actions that are allowed.
        """
        def run():
            ctx = self.ctx
            obs = ctx.obs or observations(None)
            ctx.assessment = self.ds.assess_package(ctx.event_type, obs, ctx.device_id)
            self.ensure_event().package_check = ctx.assessment.check
            return ctx.assessment.to_dict()

        return self._record("get_package_state", {}, run)

    @tool
    def update_package_state(self, action: str, reason: str) -> dict:
        """Apply a package action. Only actions listed as allowed by get_package_state are accepted.

        Args:
            action: One of "create" (a new package arrived), "mark_seen" (the package is still there),
                "mark_missing" (the package is gone from its arrival view), "no_change".
            reason: One short sentence explaining the decision, for the caregiver log.
        """
        def run():
            ctx = self.ctx
            if action not in PACKAGE_ACTIONS:
                raise ToolError(f"unknown action {action!r}; use one of {', '.join(PACKAGE_ACTIONS)}")
            if ctx.assessment is None:
                raise ToolError("call get_package_state first")
            if ctx.package_action is not None:
                return {"status": "already_applied", "package_action": ctx.package_action}
            event = self.ensure_event()
            try:
                pkg = self.ds.apply_package_action(action, ctx.assessment, event, ctx.obs or observations(None))
            except PackageStateError as exc:
                raise ToolError(str(exc)) from exc
            ctx.package_action = {"create": "created", "mark_seen": "still_present",
                                  "mark_missing": "missing"}.get(action)
            if ctx.package_action:
                ctx.assessment.open_package = pkg
            return {"status": "applied", "action": action,
                    "package": None if pkg is None else {"id": pkg.id, "status": pkg.status.value}}

        return self._record("update_package_state", {"action": action, "reason": reason}, run)

    @tool
    def get_visit_baseline(self) -> dict:
        """How unusual is a visit at this hour for this home (vehicle and motion events only).

        Returns a score from 0 (normal time) to 1 (very unusual), the threshold above which the
        caregiver should be told, and a short explanation.
        """
        def run():
            ctx = self.ctx
            if ctx.unusual is None:
                ctx.unusual = self.ds.score_event(ctx.event_type)
            ctx.unusual_checked = True
            if ctx.unusual is None:
                return {"applicable": False, "reason": f"'{ctx.event_type}' events are not scored"}
            u = ctx.unusual
            return {"applicable": True, "score": u.score, "threshold": self.ds.profile.unusual_threshold,
                    "hour": u.hour, "expected_visits_this_hour": u.expected, "average_visits_per_hour": u.average,
                    "explanation": u.explanation}

        return self._record("get_visit_baseline", {}, run)

    @tool
    def notify_resident(self, text: str, kind: str = "package_arrived") -> dict:
        """Send a message to the resident's screen. Plain, calm language; include the time; no jargon.

        Args:
            text: One or two short sentences for an older, low-vision resident.
            kind: "package_arrived" (only after the package was created in this event) or "info".
        """
        def run():
            if kind not in RESIDENT_KINDS:
                raise ToolError(f"kind must be one of {', '.join(RESIDENT_KINDS)}")
            if kind == "package_arrived" and self.ctx.package_action != "created":
                raise ToolError("package_arrived needs a package created in this event (update_package_state create)")
            return self._send("resident", kind, text)

        return self._record("notify_resident", {"text": text, "kind": kind}, run)

    @tool
    def notify_caregiver(self, text: str, kind: str) -> dict:
        """Alert the remote caregiver (sent by email). Concise and evidence-based.

        Args:
            text: What was observed, when, and why it matters, with the evidence (score, view check).
            kind: "package_missing" (only after mark_missing in this event), "unusual_hour"
                (after get_visit_baseline) or "info".
        """
        def run():
            ctx = self.ctx
            if kind not in CAREGIVER_KINDS:
                raise ToolError(f"kind must be one of {', '.join(CAREGIVER_KINDS)}")
            extra: dict[str, Any] = {}
            if kind == "package_missing":
                if ctx.package_action != "missing":
                    raise ToolError("package_missing needs the package marked missing in this event")
                pkg = ctx.assessment.open_package
                extra = {"arrived_sim_ts": pkg.arrived_sim_ts, "last_seen_sim_ts": pkg.last_seen_sim_ts,
                         "view_check": ctx.assessment.view_check}
            if kind == "unusual_hour":
                if not ctx.unusual_checked or ctx.unusual is None:
                    raise ToolError("call get_visit_baseline first (vehicle and motion events only)")
                extra = {"score": ctx.unusual.score}
            return self._send("caregiver", kind, text, extra)

        return self._record("notify_caregiver", {"text": text, "kind": kind}, run)

    @tool
    def write_daily_digest(self, day: str = "", note: str = "") -> dict:
        """Write and send the caregiver's daily digest (deliveries, pickups, missing, unusual activity).

        The facts are computed from stored records, not written by you.

        Args:
            day: Optional home-local date "YYYY-MM-DD". Empty means the 24 hours ending now.
            note: Optional one-sentence note to add, only about things the facts support.
        """
        def run():
            start, end = self.ds.digest_period(day or None)
            facts = self.ds.digest_facts(start, end)
            text = self.ds.digest_text(facts, note or None)
            n = self.ds.notify("caregiver", "daily_digest", text, event_id=None, source=self.ctx.brain,
                               extra={"period": {"start": facts["start"], "end": facts["end"]}, "facts": facts})
            self.ctx.notifications.append(n)
            return {"status": n.status, "notification_id": n.id, "text": text}

        return self._record("write_daily_digest", {"day": day, "note": note}, run)
