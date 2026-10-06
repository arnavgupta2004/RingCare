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
from typing import Any

from backend.config import current_access_token, get_settings
from backend.ring.client import RingClient
from backend.vision.analyze import analyze_event
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


async def default_device_id(access_token: str) -> str:
    """First camera/doorbell from GET /v1/devices (cached). Sensors have no video capability."""
    global _default_device_id
    if _default_device_id:
        return _default_device_id
    async with RingClient(access_token) as ring:
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


async def handle_event(event: DoorEvent) -> dict[str, Any]:
    """Single entry point for every door event: record it, then capture frames if relevant."""
    settings = get_settings()
    record: dict[str, Any] = {"event": {k: v for k, v in asdict(event).items() if k != "raw"}}

    if event.event_type in CAPTURE_EVENT_TYPES:
        token = current_access_token()
        try:
            device_id = event.device_id or await default_device_id(token)
            out_dir = settings.data_dir / "frames" / _safe_dirname(event.event_id)
            async with _capture_lock:
                result = await capture_with_retry(token, device_id, out_dir)
            record["capture"] = result.as_dict()
            if result.frames:
                analysis = await asyncio.to_thread(
                    analyze_event, event.event_id, event.event_type, out_dir, "ring_whep"
                )
                desc = analysis.get("description") or {}
                record["analysis"] = {"source": desc.get("source"), **(desc.get("result") or {})}
        except Exception as exc:
            logger.error("event %s: capture failed: %s", event.event_id, exc)
            record["capture"] = {"error": str(exc), "frame_count": 0}
    else:
        record["capture"] = None

    record["handled_at"] = datetime.now(timezone.utc).isoformat()
    with (settings.logs_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    logger.info("event %s (%s) handled: %s frames", event.event_id, event.event_type,
                (record["capture"] or {}).get("frame_count", "-"))
    return record
