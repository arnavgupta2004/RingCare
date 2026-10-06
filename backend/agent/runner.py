"""Runs events and digests through the selected brain, with fallback to the rules brain.

Brain selection (AGENT_BRAIN):
    rules    always the deterministic rules brain
    bedrock  the Strands agent on Bedrock (BEDROCK agent model: AGENT_MODEL_ID, default Claude Haiku 4.5)
    auto     (default) bedrock if scripts/check_bedrock.sh passes at startup, else rules
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.agent.brains import RulesBrain, StrandsBrain, bedrock_brain
from backend.agent.tools import AnalyzeFn, CaptureFn, DoorstepTools, EventContext
from backend.config import PROJECT_ROOT, get_settings
from backend.doorstep import Doorstep, get_doorstep

logger = logging.getLogger("agent.runner")

CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "check_bedrock.sh"


@dataclass
class BrainChoice:
    brain: RulesBrain | StrandsBrain
    why: str


def bedrock_available(timeout_s: float = 60) -> tuple[bool, str]:
    """Run scripts/check_bedrock.sh; True only if it exits 0."""
    try:
        proc = subprocess.run([str(CHECK_SCRIPT)], capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"check_bedrock.sh could not run: {exc}"
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    return proc.returncode == 0, last


def choose_brain(setting: str | None = None) -> BrainChoice:
    get_settings()  # loads .env
    setting = (setting or os.getenv("AGENT_BRAIN", "auto")).strip().lower()
    if setting == "rules":
        return BrainChoice(RulesBrain(), "AGENT_BRAIN=rules")
    if setting == "bedrock":
        return BrainChoice(bedrock_brain(), "AGENT_BRAIN=bedrock")
    if setting != "auto":
        raise ValueError(f"AGENT_BRAIN must be rules, bedrock or auto, not {setting!r}")
    ok, detail = bedrock_available()
    if ok:
        return BrainChoice(bedrock_brain(), "auto: check_bedrock.sh passed")
    return BrainChoice(RulesBrain(), f"auto: Bedrock unavailable, using rules ({detail})")


class AgentRunner:
    def __init__(self, doorstep: Doorstep, brain: RulesBrain | StrandsBrain | None = None, *,
                 why: str = "", capture: CaptureFn | None = None, analyze: AnalyzeFn | None = None):
        self.doorstep = doorstep
        self.brain = brain or RulesBrain()
        self.why = why or f"{self.brain.name} brain"
        self.fallback = RulesBrain()
        self.capture = capture
        self.analyze = analyze

    def info(self) -> dict[str, Any]:
        return {"brain": self.brain.name, "why": self.why}

    def _tools(self, ctx: EventContext) -> DoorstepTools:
        return DoorstepTools(self.doorstep, ctx, capture=self.capture, analyze=self.analyze)

    async def _run(self, ctx: EventContext, tools: DoorstepTools, method: str, *args: Any) -> None:
        ctx.brain = self.brain.name
        try:
            await getattr(self.brain, method)(tools, ctx, *args)
        except Exception as exc:
            if self.brain is self.fallback or isinstance(self.brain, RulesBrain):
                raise
            logger.error("%s brain failed (%s); falling back to rules", self.brain.name, exc)
            ctx.trace.append({"tool": "agent", "brain": self.brain.name, "ok": False,
                              "error": f"{type(exc).__name__}: {exc}",
                              "sim_ts": self.doorstep.clock.now().isoformat(timespec="seconds")})
            ctx.brain = self.fallback.name
            await getattr(self.fallback, method)(tools, ctx, *args)
            detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            detail = detail if len(detail) <= 120 else detail[:117] + "..."
            ctx.reason = f"[{self.brain.name} agent failed ({detail}); handled by rules] {ctx.reason}"

    async def handle_event(self, event_id: str, event_type: str, *, device_id: str | None = None,
                           source: str = "webhook", preloaded_analysis: dict[str, Any] | None = None) -> EventContext:
        ctx = EventContext(event_id=event_id, event_type=event_type, device_id=device_id, source=source,
                           preloaded_analysis=preloaded_analysis)
        reminders = self.doorstep.check_reminders()  # time-based, not an agent decision
        if reminders:
            ctx.trace.append({"tool": "system.check_reminders", "brain": "rules", "ok": True,
                              "output": f"{len(reminders)} reminder(s) sent",
                              "sim_ts": self.doorstep.clock.now().isoformat(timespec="seconds")})
        ctx.notifications.extend(reminders)
        tools = self._tools(ctx)
        try:
            await self._run(ctx, tools, "run_event")
        finally:
            event = tools.ensure_event()
            event.agent_brain, event.agent_reason, event.agent_trace = ctx.brain, ctx.reason, ctx.trace
            self.doorstep.save_event(event)
        return ctx

    async def write_digest(self, day: str | None = None) -> EventContext:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ctx = EventContext(event_id=f"digest-{stamp}", event_type="digest", source="digest")
        await self._run(ctx, self._tools(ctx), "run_digest", day)
        return ctx


# --- process-wide runner ---------------------------------------------------------

_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    """Rules until select_brain_at_startup() has run (it may take a few seconds)."""
    global _runner
    if _runner is None:
        _runner = AgentRunner(get_doorstep(), RulesBrain(), why="startup: brain not chosen yet",
                              capture=_live_capture, analyze=_analyze)
    return _runner


def select_brain_at_startup() -> dict[str, Any]:
    runner = get_runner()
    try:
        choice = choose_brain()
    except Exception as exc:  # never block startup on brain selection
        logger.error("brain selection failed, staying on rules: %s", exc)
        runner.brain, runner.why = RulesBrain(), f"brain selection failed: {exc}"
        return runner.info()
    runner.brain, runner.why = choice.brain, choice.why
    logger.info("agent brain: %s (%s)", choice.brain.name, choice.why)
    return runner.info()


async def _live_capture(event_id: str, event_type: str, device_id: str | None):
    from backend.events import capture_for_event

    return await capture_for_event(event_id, device_id)


def _analyze(event_id: str, event_type: str, frames_dir) -> dict[str, Any]:
    from backend.vision.analyze import analyze_event

    return analyze_event(event_id, event_type, frames_dir, "ring_whep")
