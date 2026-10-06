"""Scene description with an Anthropic Claude model on Amazon Bedrock (boto3 Converse API).

Sends 3 representative frames plus the YOLO summary and asks for strict JSON.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

logger = logging.getLogger("vision.describe")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_MODEL_ID = "us.anthropic.claude-opus-5-5"
MAX_IMAGE_WIDTH = 1024
FRAMES_PER_REQUEST = 3

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "scene_summary": str,
    "objects_present": list,
    "package_visible": bool,
    "vehicle_visible": bool,
    "people_visible": bool,
    "confidence": (int, float),
    "accessible_description": str,
}

SYSTEM_PROMPT = """You analyse doorbell camera frames for DoorSight, an assistant for elderly and low-vision residents.
You receive a few frames from one short live-video capture, in time order, plus counts from a local object detector.
The detector uses generic COCO classes: it cannot detect cardboard boxes or parcels, and it often misses small or partly hidden objects, so trust what you see in the images over the detector when they disagree.
Ignore on-screen overlays such as logos, device IDs, partner names and timestamps.
Describe only what is visible. Do not guess identities, intentions or anything outside the frames."""

USER_PROMPT = """Event type reported by the camera: {event_type}
Local detector summary (per class: max count in any frame, indices of frames where it appears):
{yolo_summary}

Return ONLY a JSON object, no prose and no code fences, with exactly these fields:
{{
  "scene_summary": string, 1-3 factual sentences about the scene and anything that changes across the frames,
  "objects_present": array of short lowercase noun phrases for notable objects (e.g. "cardboard package", "parked car"),
  "package_visible": boolean, true if a parcel, box, envelope or delivery bag is visible near the door,
  "vehicle_visible": boolean,
  "people_visible": boolean,
  "confidence": number from 0 to 1, your confidence in the booleans above,
  "accessible_description": string, ONE plain, calm sentence a low-vision older resident would understand when read aloud; no jargon, no alarm, no camera terms
}}"""


class DescribeError(RuntimeError):
    pass


def pick_representative_frames(frames: list[Path], detections: dict[str, Any] | None, k: int = FRAMES_PER_REQUEST) -> list[Path]:
    """First, last, and the frame with the most relevant detections (else the middle one)."""
    if len(frames) <= k:
        return list(frames)
    picks = {0, len(frames) - 1}
    best_idx, best_score = len(frames) // 2, 0
    for f in (detections or {}).get("frames", []):
        score = sum(f["counts"].values())
        if score > best_score and f["index"] not in picks:
            best_idx, best_score = f["index"], score
    picks.add(best_idx)
    return [frames[i] for i in sorted(picks)][:k]


def _jpeg_bytes(path: Path) -> bytes:
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > MAX_IMAGE_WIDTH:
            img = img.resize((MAX_IMAGE_WIDTH, round(img.height * MAX_IMAGE_WIDTH / img.width)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def _compact_summary(detections: dict[str, Any] | None) -> str:
    if not detections:
        return "(detector not run)"
    s = detections["summary"]
    return json.dumps({g: {"max_count": v["max_count"], "frames_present": v["frames_present"]} for g, v in s.items()})


def parse_strict_json(text: str) -> dict[str, Any]:
    """Parse the model's reply and validate the required fields and types."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DescribeError(f"reply is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DescribeError("reply is not a JSON object")
    for field, typ in REQUIRED_FIELDS.items():
        if field not in data:
            raise DescribeError(f"missing field: {field}")
        if isinstance(data[field], bool) and typ in ((int, float),):
            raise DescribeError(f"field {field} must be a number")
        if not isinstance(data[field], typ):
            raise DescribeError(f"field {field} has wrong type {type(data[field]).__name__}")
    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
    return {k: data[k] for k in REQUIRED_FIELDS}


def describe_scene(
    frames: list[Path],
    event_type: str,
    detections: dict[str, Any] | None,
    model_id: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Call Bedrock Converse with representative frames + YOLO summary; return validated JSON + metadata."""
    model_id = model_id or os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    client = client or boto3.client(
        "bedrock-runtime", region_name=AWS_REGION,
        config=Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 2}),
    )
    chosen = pick_representative_frames(frames, detections)
    if not chosen:
        raise DescribeError("no frames to describe")

    content: list[dict[str, Any]] = []
    for i, path in enumerate(chosen, 1):
        content.append({"text": f"Frame {i} of {len(chosen)} ({path.name}):"})
        content.append({"image": {"format": "jpeg", "source": {"bytes": _jpeg_bytes(path)}}})
    content.append({"text": USER_PROMPT.format(event_type=event_type, yolo_summary=_compact_summary(detections))})
    messages = [{"role": "user", "content": content}]

    last_error: Exception | None = None
    for attempt in (1, 2):
        logger.info("-> bedrock converse %s (%d frames, attempt %d)", model_id, len(chosen), attempt)
        try:
            response = client.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                inferenceConfig={"maxTokens": 8000},
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error("x  bedrock converse failed: %s", exc)
            raise DescribeError(f"Bedrock call failed: {exc}") from exc
        logger.info("<- bedrock converse stopReason=%s usage=%s", response.get("stopReason"), response.get("usage"))

        blocks = response["output"]["message"]["content"]
        text = "".join(b["text"] for b in blocks if "text" in b)
        try:
            result = parse_strict_json(text)
            return {
                "model_id": model_id,
                "frames_sent": [p.name for p in chosen],
                "usage": response.get("usage"),
                "result": result,
            }
        except DescribeError as exc:
            last_error = exc
            logger.warning("bedrock reply rejected (%s); asking once more", exc)
            messages = messages + [
                {"role": "assistant", "content": [{"text": text or "(empty)"}]},
                {"role": "user", "content": [{"text": f"That reply was invalid ({exc}). Reply with only the JSON object."}]},
            ]
    raise DescribeError(f"no valid JSON after 2 attempts: {last_error}")
