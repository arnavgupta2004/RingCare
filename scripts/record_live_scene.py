"""Record the package scene live: Ring Playground click → DoorSight live WHEP capture → detection.

    python scripts/record_demo.py --live --scene package     (or run this file directly)

Opens a visible Chrome window with its own profile (video/.chrome-profile). You sign in to the
Ring developer console yourself and open the Playground in that same tab; the script prompts you
when to click Package. It then triggers a real capture through DoorSight's /simulate-event, waits
for the agent, draws YOLO-World's detection on the fresh frame, and shows the resident view and
the agent's reasoning. Everything is one Playwright recording with timestamped markers:

    video/raw/live_package.webm     1920x1080 recording of the whole session
    video/live_timeline.json        marker offsets (seconds from the start of that recording)

scripts/splice_live_scene.py then cuts the scene from it and splices it into the full take.
"""

from __future__ import annotations

import base64
import getpass
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.record_demo import API, RAW_DIR, SCENES_DIR, WEB, check_services, post  # noqa: E402

PROFILE_DIR = ROOT / "video" / ".chrome-profile"
RING_CONSOLE = "https://developer.amazon.com/ring/console"
CAPTURE_TIMEOUT_S = 150

# DoorSight and scene pages at 150% (a 1280x720 layout rendered at 1080p); the Ring console at 125%.
# Real clicks get a brief ripple, because screen recordings don't show the mouse cursor.
INIT_SCRIPT = """
document.addEventListener('DOMContentLoaded', () => {
  if (window.top !== window) return;
  const local = location.protocol === 'file:' || location.hostname === 'localhost';
  document.documentElement.style.zoom = local ? '1.5' : '1.25';
});
document.addEventListener('pointerdown', (e) => {
  const r = document.createElement('div');
  r.style.cssText = `position:fixed;left:${e.clientX - 22}px;top:${e.clientY - 22}px;width:44px;height:44px;
    border:4px solid #ffcc00;border-radius:50%;pointer-events:none;z-index:2147483647;
    transition:transform .6s ease-out,opacity .6s ease-out;`;
  document.body.appendChild(r);
  requestAnimationFrame(() => { r.style.transform = 'scale(2.2)'; r.style.opacity = '0'; });
  setTimeout(() => r.remove(), 700);
}, true);
"""


def get_state() -> dict:
    with urllib.request.urlopen(API + "/state", timeout=30) as r:
        return json.loads(r.read())


def token_minutes_left() -> float:
    from backend.config import current_access_token

    try:
        exp = json.loads(base64.urlsafe_b64decode(current_access_token().split(".")[1] + "=="))["exp"]
    except (IndexError, ValueError, KeyError):
        return 0.0
    return (exp - time.time()) / 60


def refresh_token_prompt() -> None:
    """Let the user paste a fresh sandbox token (hidden input) right before the capture."""
    left = token_minutes_left()
    pasted = getpass.getpass(
        f"\n>>> Sandbox token: {left:.0f} min left. Generate a fresh one in the Playground and paste it here\n"
        "    (input is hidden; just press Enter to keep the current one): ").strip()
    if pasted:
        env = ROOT / ".env"
        lines = [l for l in env.read_text().splitlines() if not l.startswith("RING_ACCESS_TOKEN=")]
        env.write_text("\n".join(lines + [f"RING_ACCESS_TOKEN={pasted}"]) + "\n")
    left = token_minutes_left()
    print(f"    token: {left:.0f} min left")
    if left < 3:
        raise SystemExit("The sandbox token expires in under 3 minutes; generate a fresh one and run this again.")


def annotate_live_frame(event: dict) -> dict:
    """Draw YOLO-World's detections on the event's representative (fresh) frame for the video."""
    from backend.vision.detect import _load_model

    ref = event.get("snapshot") or ""
    rel = ref.split("/", 3)[3] if ref.startswith("s3://") else ref  # s3://bucket/frames/... -> frames/...
    frame = ROOT / "data" / rel
    if not frame.exists():
        raise SystemExit(f"live frame not found on disk: {frame}")
    result = _load_model().predict(str(frame), conf=0.25, verbose=False)[0]
    boxes = [(result.names[int(b.cls)], round(float(b.conf), 2)) for b in result.boxes]
    result.save(filename=str(SCENES_DIR / "live_yolo.jpg"), line_width=4, font_size=28)
    best = max((c for n, c in boxes if n in ("cardboard box", "package", "parcel")), default=None)
    label = (f"YOLO-World on this frame, run locally: <b>“cardboard box” {best:.2f}</b>" if best is not None
             else "YOLO-World on this frame, run locally: no package detected")
    when = datetime.fromisoformat(event["real_ts"]).strftime("%H:%M:%S")
    (SCENES_DIR / "live_yolo.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Live frame</title><link rel="stylesheet" href="base.css"></head>
<body><div class="frame"><img src="live_yolo.jpg" alt="Frame just captured live from the Ring sandbox, with YOLO-World detections">
<div class="label">Frame captured live from the Ring sandbox at {when} · {label}</div></div></body></html>""")
    return {"frame": str(frame.relative_to(ROOT)), "detections": boxes, "package_confidence": best}


def run() -> None:
    from playwright.sync_api import sync_playwright

    check_services(live=False)  # the token is checked (and can be replaced) right before the capture
    print(f"Sandbox token: {token_minutes_left():.0f} minutes left (you can paste a fresh one after signing in)")
    print("Loading YOLO-World (for drawing the live frame)…")
    from backend.vision.detect import _load_model
    _load_model()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for old in [*RAW_DIR.glob("live_*.webm"), *RAW_DIR.glob("page@*.webm")]:
        old.unlink()
    marks: dict[str, float] = {}

    t_ref = time.monotonic()
    pages: list[dict] = []  # every tab gets its own recording; remember when each started

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=False,
            viewport={"width": 1920, "height": 1080}, screen={"width": 1920, "height": 1080},
            record_video_dir=str(RAW_DIR), record_video_size={"width": 1920, "height": 1080})
        context.add_init_script(INIT_SCRIPT)
        context.on("page", lambda p: pages.append({"page": p, "start": round(time.monotonic() - t_ref, 3)}))
        for p in context.pages:
            pages.append({"page": p, "start": 0.0})
        page = context.pages[0] if context.pages else context.new_page()

        def mark(name: str) -> None:
            marks[name] = round(time.monotonic() - t_ref, 3)  # seconds since the browser started
            print(f"  [{marks[name]:7.1f}s] {name}")

        page.goto(RING_CONSOLE)
        input("\n>>> In the Chrome window: sign in to the Ring developer console (if asked) and open the\n"
              "    Playground. Then press Enter here… ")
        mark("playground_ready")
        console = next((p["page"] for p in pages if "amazon" in (p["page"].url or "")), page)
        refresh_token_prompt()

        # Fresh state and clock, as in the full take (package arrives at 2:10 PM).
        from backend.store import store_from_env
        store_from_env().clear()
        post("/demo/clock", {"set": datetime.now().replace(hour=14, minute=9, second=55).strftime("%Y-%m-%dT%H:%M:%S")})
        before = {e["event_id"] for e in get_state()["events"]}

        input("\n>>> Now CLICK 'Package' in the Playground. As soon as you've clicked, press Enter here… ")
        mark("playground_clicked")
        console = next((p["page"] for p in pages if "amazon" in (p["page"].url or "")), console)
        time.sleep(6)  # let the clip switch on screen (and avoid the first-capture stall)

        page.goto(f"{WEB}/caregiver", wait_until="domcontentloaded")
        page.wait_for_selector("text=Demo controls")
        time.sleep(1.0)
        mark("doorsight_open")
        page.get_by_role("button", name="Simulate package").click()
        mark("trigger")

        deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        event = None
        while time.monotonic() < deadline:
            events = [e for e in get_state()["events"] if e["event_id"] not in before and e["event_type"] == "package"]
            if events:
                event = events[-1]
                break
            time.sleep(1.0)
        if event is None:
            context.close()
            raise SystemExit("The live capture didn't finish in time; check the server log and try again.")
        time.sleep(1.5)  # one UI refresh so the new event and status are on screen
        mark("done")
        print(f"  live event {event['event_id']}: {event['frame_count']} frames, package seen: {event['package_seen']}")
        if not event["frame_count"]:
            context.close()
            raise SystemExit("No frames came back (token expired or stream stalled). Regenerate the token / try again.")

        live = annotate_live_frame(event)
        page.goto((SCENES_DIR / "live_yolo.html").as_uri())
        page.wait_for_load_state("load")
        mark("yolo")
        time.sleep(7.5)

        page.goto(f"{WEB}/resident", wait_until="domcontentloaded")
        page.wait_for_selector("main h2")
        mark("resident")
        time.sleep(7.0)

        page.goto(f"{WEB}/caregiver", wait_until="domcontentloaded")
        page.wait_for_selector("text=Door activity")
        row = page.locator("section.activity tbody tr", has_text="package").first
        row.scroll_into_view_if_needed()
        row.locator("summary").click()
        row.scroll_into_view_if_needed()
        mark("reasoning")
        time.sleep(9.5)
        mark("end")

        starts = {id(p["page"]): p["start"] for p in pages}
        sources = {"doorsight": (page, RAW_DIR / "live_package.webm"),
                   "console": (console, RAW_DIR / "live_console.webm")}
        videos = {role: (p.video, starts.get(id(p), 0.0), target) for role, (p, target) in sources.items()}
        context.close()
        files = {}
        for role, (video, start, target) in videos.items():
            path = Path(video.path())
            if path.exists() and not target.exists():
                path.rename(target)
            elif not path.exists() and role == "console":
                target = RAW_DIR / "live_package.webm"  # same tab as DoorSight
            files[role] = {"file": target.name, "start_s": start}

    (ROOT / "video" / "live_timeline.json").write_text(json.dumps(
        {"marks_s": marks, "files": files,
         "event": {k: event.get(k) for k in ("event_id", "frame_count", "package_seen",
                                             "frame_source", "agent_brain", "agent_reason")},
         "live_frame": live}, indent=2, default=str))
    for old in RAW_DIR.glob("page@*.webm"):
        old.unlink()
    print(f"\nrecordings: {files}\nmarkers: video/live_timeline.json\nNext: python scripts/splice_live_scene.py")


if __name__ == "__main__":
    run()
