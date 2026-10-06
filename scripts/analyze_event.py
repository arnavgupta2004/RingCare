"""Run step-3 analysis (YOLO + Bedrock) on an existing capture.

Usage:
    python scripts/analyze_event.py data/frames/sim-package-1791250581919 [--event-type package]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.vision.analyze import analysis_path, analyze_event  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("--event-type", help="defaults to the type in a sim-<type>-<ts> directory name")
    parser.add_argument("--full", action="store_true", help="print per-frame detections too")
    args = parser.parse_args()

    event_id = args.frames_dir.name
    event_type = args.event_type or (event_id.split("-")[1] if event_id.startswith("sim-") else "unknown")
    record = analyze_event(event_id, event_type, args.frames_dir)
    if not args.full and "detections" in record:
        record["detections"] = {k: v for k, v in record["detections"].items() if k != "frames"}
    print(json.dumps(record, indent=2, default=str))
    print(f"\nsaved: {analysis_path(event_id)}")
    return 0 if "description" in record else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
