"""Local object detection on captured frames with YOLO-World (open-vocabulary, ultralytics).

Plain COCO YOLO has no box/parcel class and missed the package on the sandbox clip
entirely (DECISIONS.md D1), so we prompt YOLO-World with our own class names.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.config import PROJECT_ROOT

logger = logging.getLogger("vision.detect")

MODEL_NAME = "yolov8s-worldv2.pt"
MODEL_PATH = PROJECT_ROOT / "data" / "models" / MODEL_NAME
CONF_THRESHOLD = 0.25

# Open-vocabulary prompt classes -> our group
GROUPS: dict[str, str] = {
    "cardboard box": "package",
    "package": "package",
    "parcel": "package",
    "person": "person",
    "car": "vehicle",
    "truck": "vehicle",
    "van": "vehicle",
}
CLASSES = list(GROUPS)
GROUP_NAMES = ("person", "package", "vehicle")

_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from ultralytics import YOLOWorld
            from ultralytics.utils.downloads import attempt_download_asset

            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            attempt_download_asset(MODEL_PATH)  # no-op if already present
            _model = YOLOWorld(str(MODEL_PATH))
            _model.set_classes(CLASSES)
            logger.info("loaded %s with classes %s", MODEL_PATH.name, CLASSES)
    return _model


def detect_frames(frames: list[Path], conf: float = CONF_THRESHOLD) -> dict[str, Any]:
    """Run YOLO on each frame. Returns per-frame detections plus an event-level summary."""
    model = _load_model()
    per_frame: list[dict[str, Any]] = []
    max_counts = {g: 0 for g in GROUP_NAMES}
    frames_with = {g: [] for g in GROUP_NAMES}
    best_conf = {g: 0.0 for g in GROUP_NAMES}

    for idx, path in enumerate(frames):
        result = model.predict(str(path), conf=conf, verbose=False)[0]
        names = result.names
        detections = []
        counts: dict[str, int] = defaultdict(int)
        for box in result.boxes:
            label = names[int(box.cls)]
            group = GROUPS.get(label)
            if group is None:
                continue
            score = float(box.conf)
            x1, y1, x2, y2 = (round(v, 1) for v in box.xyxy[0].tolist())
            detections.append({"label": label, "group": group, "confidence": round(score, 3),
                               "box_xyxy": [x1, y1, x2, y2]})
            counts[group] += 1
            best_conf[group] = max(best_conf[group], score)
        for g in GROUP_NAMES:
            if counts[g]:
                max_counts[g] = max(max_counts[g], counts[g])
                frames_with[g].append(idx)
        per_frame.append({"frame": path.name, "index": idx,
                          "counts": {g: counts[g] for g in GROUP_NAMES}, "detections": detections})

    summary = {
        g: {
            "max_count": max_counts[g],
            "frames_present": frames_with[g],
            "frame_fraction": round(len(frames_with[g]) / len(frames), 2) if frames else 0.0,
            "best_confidence": round(best_conf[g], 3),
        }
        for g in GROUP_NAMES
    }
    return {"model": MODEL_NAME, "classes": CLASSES, "conf_threshold": conf, "frame_count": len(frames),
            "summary": summary, "frames": per_frame}
