"""Normalized door events and the single handler shared by /webhook and /simulate-event."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agent.runner import get_runner
from backend.config import get_settings
from backend.ring.accounts import get_accounts
from backend.ring.client import RingClient
from backend.vision.capture import capture_with_retry

logger = logging.getLogger("events")

# Event kinds we act on. Ring's motion_detected subTypes are motion|human|vehicle|other_motion;
# no package type is documented, so "package" only comes from /simulate-event for now.
CAPTURE_EVENT_TYPES = {"package", "vehicle", "motion", "human", "other_motion", "button_press"}
SIMULATABLE = ("package", "vehicle", "motion")

_capture_lock = asyncio.Lock()  # one live session per process at a time
_default_device_id: str | None = None


@dataclass
class DoorEvent:
    event_id: str
    event_type: str  # package | vehicle | motion | human | other_motion | button_press | <raw ring type>
    device_id: str | None
    timestamp_ms: int
    account_id: str | None
    source: str  # "webhook" | "simulated"
    raw: dict[str, Any]


def normalize(payload: dict[str, Any], source: str = "webhook") -> DoorEvent:
    """Turn a Ring v1.1 webhook payload into a DoorEvent."""
    data = payload.get("data") or {}
    meta = payload.get("meta") or {}
    attrs = data.get("attributes") or {}
    ring_type = data.get("type") or "unknown"
    if ring_type == "motion_detected":
        event_type = data.get("subType") or "motion"
    else:
        event_type = ring_type
    return DoorEvent(
        event_id=str(data.get("id") or uuid.uuid4().hex),
        event_type=event_type,
        device_id=attrs.get("source"),
        timestamp_ms=int(attrs.get("timestamp") or time.time() * 1000),
        account_id=meta.get("account_id"),
        source=source,
        raw=payload,
    )


def build_simulated_payload(event_type: str, device_id: str | None) -> dict[str, Any]:
    """A v1.1-shaped payload (same envelope as a real webhook) for a simulated event."""
    now_ms = int(time.time() * 1000)
    event_id = f"sim-{event_type}-{now_ms}"
    device_path = f"/v1/devices/{device_id}" if device_id else None
    return {
        "meta": {
            "version": "1.1",
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request_id": f"sim-{uuid.uuid4().hex}",
            "account_id": None,
        },
        "data": {
            "id": event_id,
            "type": "motion_detected",
            "subType": event_type,
            "attributes": {"source": device_id, "source_type": "devices", "timestamp": now_ms},
            "relationships": {"devices": {"links": {"self": device_path}}},
        },
    }


async def default_device_id(access_token: str, token_source: str = "sandbox") -> str:
    """First camera/doorbell from GET /v1/devices (cached). Sensors have no video capability."""
    global _default_device_id
    if _default_device_id:
        return _default_device_id
    async with RingClient(access_token, source=token_source) as ring:
        result = await ring.list_devices(include=["capabilities"])
    caps = {i["id"]: i.get("attributes", {}) for i in result.get("included", []) if i.get("type") == "device-capabilities"}
    for device in result.get("data", []):
        cap_id = device.get("relationships", {}).get("capabilities", {}).get("data", {}).get("id")
        if caps.get(cap_id, {}).get("video"):
            _default_device_id = device["id"]
            logger.info("default device: %s (%s)", device.get("attributes", {}).get("name"), device["id"])
            return _default_device_id
    raise RuntimeError("no video-capable Ring device found")


def _safe_dirname(event_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", event_id)[:120]


async def capture_for_event(event_id: str, device_id: str | None) -> Path | None:
    """Live WHEP capture for an event (one session at a time). Returns the frames directory."""
    settings = get_settings()
    out_dir = settings.data_dir / "frames" / _safe_dirname(event_id)

    async def run(token: str, source: str):
        device = device_id or await default_device_id(token, source)
        async with _capture_lock:
            return await capture_with_retry(token, device, out_dir, token_source=source)

    # Linked-account token when present (refreshed on expiry / 401), else the sandbox token.
    result = await get_accounts().with_token(run)
    if result.error:
        logger.warning("event %s: capture error: %s", event_id, result.error)
    return out_dir if result.frames else None


# Account / device lifecycle webhooks: not door activity, so they don't go to the agent.
LIFECYCLE_EVENT_TYPES = {
    "app_integration_added", "app_integration_removed", "device_added", "device_removed",
    "device_online", "device_offline", "subscription_activated", "subscription_deactivated",
}


async def handle_event(event: DoorEvent) -> dict[str, Any]:
    """Single entry point for every Ring event (webhook or simulated)."""
    settings = get_settings()
    record: dict[str, Any] = {"event": {k: v for k, v in asdict(event).items() if k != "raw"}}
    if event.event_type in LIFECYCLE_EVENT_TYPES:
        record["lifecycle"] = _handle_lifecycle(event)
        record["handled_at"] = datetime.now(timezone.utc).isoformat()
        with (settings.logs_dir / "events.jsonl").open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return record
    try:
        ctx = await get_runner().handle_event(
            event.event_id, event.event_type, device_id=event.device_id, source=event.source)
        record["agent"] = {
            "brain": ctx.brain,
            "reason": ctx.reason,
            "tools": [t["tool"] + ("" if t.get("ok") else " (error)") for t in ctx.trace],
            "package_action": ctx.package_action,
            "unusual_score": ctx.unusual.score if ctx.unusual else None,
            "notifications": [{"audience": n.audience, "kind": n.kind, "text": n.text} for n in ctx.notifications],
            "description_source": (ctx.obs or {}).get("description_source"),
        }
    except Exception as exc:
        logger.exception("event %s: agent failed", event.event_id)
        record["agent"] = {"error": str(exc)}

    record["handled_at"] = datetime.now(timezone.utc).isoformat()
    with (settings.logs_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    logger.info("event %s (%s) handled by %s", event.event_id, event.event_type,
                (record["agent"] or {}).get("brain", "error"))
    return record


def _handle_lifecycle(event: DoorEvent) -> dict[str, Any]:
    account_id = event.account_id or (event.raw.get("data", {}).get("attributes", {}) or {}).get("source")
    if event.event_type == "app_integration_removed" and account_id:
        # Ring already revoked the tokens; delete ours right away.
        return {"action": "tokens_deleted" if get_accounts().removed_by_ring(account_id) else "unknown_account"}
    logger.info("lifecycle event %s for %s", event.event_type, account_id)
    return {"action": "logged"}
