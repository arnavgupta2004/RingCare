"""Splice the live package scene into the full demo take.

Inputs:  video/raw/demo_raw.webm + video/timeline.json            (full take, scripts/record_demo.py)
         video/raw/live_package.webm + video/live_timeline.json   (live scene, --live --scene package)
Outputs: video/raw/demo_composite.mp4 + video/timeline_composite.json

The live scene is cut from its recording in six beats, all carrying a "LIVE from Ring sandbox"
badge: the Playground click, the DoorSight trigger, the result arriving (with an on-screen note
saying how many seconds of capture wait were cut), the live frame with YOLO-World's box, the
resident view, and the agent's reasoning. Then run:

    python scripts/make_video.py --raw video/raw/demo_composite.mp4 --timeline video/timeline_composite.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_video import FFMPEG, MAX_TOTAL_S, duration  # noqa: E402
from scripts.record_demo import load_scenes  # noqa: E402

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-an"]
BADGE = (f"drawtext=fontfile='{FONT}':text='LIVE from Ring sandbox':fontsize=34:fontcolor=white:"
         "box=1:boxcolor=0xD7263D@0.92:boxborderw=14:x=w-tw-44:y=40")

# (marker, start offset, end offset) in seconds, relative to markers in live_timeline.json
BEATS = [
    ("click",     "playground_clicked", -3.0, 5.0),   # the Playground, the Package click, the clip switching
    ("trigger",   "trigger",           -2.0, 4.0),   # DoorSight: "Simulate package" → capturing live video
    ("result",    "done",              -1.5, 1.5),   # the new event lands (wait in between is cut)
    ("yolo",      "yolo",               0.3, 6.8),   # fresh frame with YOLO-World's box
    ("resident",  "resident",           0.3, 6.3),   # "A package was left at your door…"
    ("reasoning", "reasoning",          0.3, 8.8),   # the agent's tool calls and reason
]


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def main() -> int:
    full = json.loads((ROOT / "video" / "timeline.json").read_text())["scene_offsets_s"]
    live = json.loads((ROOT / "video" / "live_timeline.json").read_text())
    marks = live["marks_s"]
    full_raw, live_raw = ROOT / "video" / "raw" / "demo_raw.webm", ROOT / "video" / "raw" / "live_package.webm"
    scenes = load_scenes()
    order = [s["id"] for s in scenes]
    i_pkg = order.index("package")
    origin = full[order[0]]
    old_pkg_len = full[order[i_pkg + 1]] - full["package"]
    end_full = full[order[-1]] + (scenes[-1]["end"] - scenes[-1]["start"])

    work = Path(tempfile.mkdtemp(prefix="doorsight-splice-"))
    parts: list[Path] = []

    # 1. full take up to the package scene
    pre = work / "00_pre.mp4"
    ffmpeg("-ss", f"{origin:.3f}", "-t", f"{full['package'] - origin:.3f}", "-i", str(full_raw),
           "-vf", "fps=30,scale=1920:1080", *ENCODE, str(pre))
    parts.append(pre)

    # 2. the live package scene, beat by beat
    cut_s = (marks["done"] - 1.5) - (marks["trigger"] + 4.0)
    pkg_len = 0.0
    for n, (name, marker, a, b) in enumerate(BEATS, start=1):
        start = max(0.0, marks[marker] + a)
        if name == "click":
            start = max(start, marks["playground_ready"])
        length = marks[marker] + b - start
        vf = f"fps=30,scale=1920:1080,{BADGE}"
        if name == "result" and cut_s > 1:
            vf += (f",drawtext=fontfile='{FONT}':text='live capture shortened by {cut_s:.0f} s':fontsize=30:"
                   "fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=12:x=44:y=40")
        out = work / f"{n:02d}_{name}.mp4"
        ffmpeg("-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(live_raw), "-vf", vf, *ENCODE, str(out))
        parts.append(out)
        pkg_len += duration(out)
        print(f"live beat {name:9s} {length:5.1f}s")

    # 3. full take from the pickup scene to the end
    post = work / "99_post.mp4"
    after = full[order[i_pkg + 1]]
    ffmpeg("-ss", f"{after:.3f}", "-t", f"{end_full - after:.3f}", "-i", str(full_raw),
           "-vf", "fps=30,scale=1920:1080", *ENCODE, str(post))
    parts.append(post)

    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    composite = ROOT / "video" / "raw" / "demo_composite.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(composite))

    shift = pkg_len - old_pkg_len
    offsets = {}
    for k, sid in enumerate(order):
        rel = full[sid] - origin
        offsets[sid] = round(rel if k <= i_pkg else rel + shift, 3)
    total = duration(composite)
    (ROOT / "video" / "timeline_composite.json").write_text(json.dumps(
        {"mode": "replay take with live package scene", "scene_offsets_s": offsets,
         "live_capture_cut_s": round(cut_s, 1), "total_s": round(total, 2)}, indent=2))
    print(f"\nlive package scene: {pkg_len:.1f}s (replayed scene was {old_pkg_len:.1f}s; "
          f"{cut_s:.0f}s of capture wait cut)\ncomposite: {composite.relative_to(ROOT)} {total:.1f}s")
    if total > MAX_TOTAL_S:
        print(f"WARNING: {total:.1f}s is over {MAX_TOTAL_S:.0f}s; shorten a beat in BEATS", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
