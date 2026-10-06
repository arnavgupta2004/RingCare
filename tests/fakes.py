"""A scripted Strands model: plays back pre-written tool calls so the real agent loop runs without Bedrock."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from strands.models import Model


class ScriptedModel(Model):
    """Each turn is {"tools": [(name, args), ...]} and/or {"text": "..."}, {"raise": Exception}
    or {"sleep": seconds} (to test timeouts).

    One turn is consumed per model call. `requests` records what the agent sent each time,
    including the tool results the model would have seen.
    """

    def __init__(self, turns: list[dict[str, Any]]):
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def get_config(self) -> dict[str, Any]:
        return {"model_id": "scripted"}

    def update_config(self, **kwargs: Any) -> None:
        pass

    async def structured_output(self, *args: Any, **kwargs: Any):  # pragma: no cover - not used
        raise NotImplementedError

    def tool_results(self) -> list[dict[str, Any]]:
        """Every toolResult block the agent sent back to the model, in order."""
        out = []
        for req in self.requests:
            for msg in req["messages"]:
                for block in msg["content"]:
                    if "toolResult" in block and block["toolResult"] not in out:
                        out.append(block["toolResult"])
        return out

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.requests.append({"messages": json.loads(json.dumps(messages, default=str)),
                              "tools": [t["name"] for t in tool_specs or []], "system_prompt": system_prompt})
        if not self.turns:
            raise AssertionError("ScriptedModel ran out of turns")
        turn = self.turns.pop(0)
        if "raise" in turn:
            raise turn["raise"]
        if "sleep" in turn:
            await asyncio.sleep(turn["sleep"])
        yield {"messageStart": {"role": "assistant"}}
        for i, (name, args) in enumerate(turn.get("tools", [])):
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"call-{len(self.requests)}-{i}",
                                                                "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
        if turn.get("text"):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": turn["text"]}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if turn.get("tools") else "end_turn"}}
