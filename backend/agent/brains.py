"""Agent brains: who decides which tools to call.

RulesBrain    deterministic; reproduces the doorstep rules through the same tool calls (source=rules)
StrandsBrain  a Strands agent on a model (Bedrock in production, a scripted fake model in tests)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from strands import Agent

from backend.agent.prompts import SYSTEM_PROMPT, digest_prompt, event_prompt
from backend.agent.tools import CAPTURE_EVENT_TYPES, DoorstepTools, EventContext, ToolError
from backend.doorstep import SCORED_EVENT_TYPES, arrival_text, fmt_time, missing_text, unusual_text

logger = logging.getLogger("agent.brains")

DEFAULT_AGENT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AGENT_TIMEOUT_S = float(os.getenv("AGENT_TIMEOUT_S", "120"))


class RulesBrain:
    name = "rules"

    async def run_event(self, tools: DoorstepTools, ctx: EventContext) -> None:
        notes: list[str] = []
        if ctx.event_type in CAPTURE_EVENT_TYPES:
            try:
                await tools.start_live_capture()
                scene = await tools.describe_scene()
                seen = [g for g, v in scene["seen"].items() if v]
                notes.append(f"Capture shows {', '.join(seen) if seen else 'nothing tracked'}"
                             f" ({scene['frames']} frames, description: {scene['description_source']}).")
            except ToolError as exc:
                notes.append(f"No usable capture ({exc}).")

        state = tools.get_package_state()
        action = state["recommended_action"]
        tools.update_package_state(action, state["explanation"])
        now = tools.ds.clock.now()
        if ctx.package_action == "created":
            tools.notify_resident(arrival_text(now), "package_arrived")
            notes.append("New package recorded; resident told.")
        elif ctx.package_action == "missing":
            tools.notify_caregiver(missing_text(ctx.assessment.open_package, now), "package_missing")
            notes.append("Package gone from its arrival view without a pickup; caregiver alerted.")
        elif ctx.package_action == "still_present":
            notes.append("Package still in place.")
        elif state["check"]:
            notes.append(f"Package: {state['check']}; left unchanged.")

        if ctx.event_type in SCORED_EVENT_TYPES:
            base = tools.get_visit_baseline()
            if base["applicable"] and base["score"] >= base["threshold"]:
                tools.notify_caregiver(unusual_text(ctx.unusual), "unusual_hour")
                notes.append(f"Unusual hour (score {base['score']:.2f} ≥ {base['threshold']}); caregiver alerted.")
            elif base["applicable"]:
                notes.append(f"Normal hour for visits (score {base['score']:.2f}).")
        ctx.reason = " ".join(notes) or "Nothing to act on."

    async def run_digest(self, tools: DoorstepTools, ctx: EventContext, day: str | None) -> None:
        tools.write_daily_digest(day or "", "")
        ctx.reason = "Daily digest written from stored records."


class StrandsBrain:
    """A Strands agent; `model` is a Strands Model (BedrockModel, or a fake in tests)."""

    def __init__(self, model: Any, name: str = "bedrock", timeout_s: float = AGENT_TIMEOUT_S):
        self.model = model
        self.name = name
        self.timeout_s = timeout_s

    def _agent(self, tools: DoorstepTools) -> Agent:
        return Agent(model=self.model, tools=tools.all(), system_prompt=SYSTEM_PROMPT, callback_handler=None)

    async def run_event(self, tools: DoorstepTools, ctx: EventContext) -> None:
        now = tools.ds.clock.now()
        prompt = event_prompt(ctx.event_type, f"{fmt_time(now)} on {now:%A}", ctx.source,
                              ctx.preloaded_analysis is not None)
        result = await asyncio.wait_for(self._agent(tools).invoke_async(prompt), self.timeout_s)
        ctx.reason = str(result).strip() or "(no reason given)"

    async def run_digest(self, tools: DoorstepTools, ctx: EventContext, day: str | None) -> None:
        start, end = tools.ds.digest_period(day)
        period = f"{day}" if day else f"the 24 hours ending {fmt_time(end)} on {end:%A}"
        result = await asyncio.wait_for(self._agent(tools).invoke_async(digest_prompt(period)), self.timeout_s)
        if not any(n.kind == "daily_digest" for n in ctx.notifications):
            raise ToolError("the agent did not call write_daily_digest")
        ctx.reason = str(result).strip()


def bedrock_brain(model_id: str | None = None) -> StrandsBrain:
    from strands.models import BedrockModel

    model = BedrockModel(
        model_id=model_id or os.getenv("AGENT_MODEL_ID") or DEFAULT_AGENT_MODEL_ID,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        max_tokens=2000,
    )
    return StrandsBrain(model, name="bedrock")

