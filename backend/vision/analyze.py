"""Step 3 pipeline: YOLO detections + Bedrock scene description -> data/analysis/<event_id>.json."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.vision.describe import DescribeError, describe_scene
from backend.vision.detect import detect_frames

logger = logging.getLogger("vision.analyze")


def analysis_path(event_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", event_id)[:120]
    return get_settings().data_dir / "analysis" / f"{safe}.json"


def analyze_event(event_id: str, event_type: str, frames_dir: Path, source: str = "ring_whep") -> dict[str, Any]:
    """Run detection + description on one event's frames and save the result (blocking)."""
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    record: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "frame_source": source,
        "frames_dir": str(frames_dir),
        "frame_count": len(frames),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    if not frames:
        record["error"] = "no frames"
    else:
        detections = detect_frames(frames)
        record["detections"] = detections
        try:
            record["description"] = describe_scene(frames, event_type, detections)
        except DescribeError as exc:
            logger.error("event %s: description failed: %s", event_id, exc)
            record["description"] = {"error": str(exc)}

    out = analysis_path(event_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2))
    logger.info("analysis saved: %s", out)
    return record
