"""Record the DoorSight demo with Playwright, following docs/VIDEO_SCRIPT.md.

Needs the backend (:8000) and web app (:5180) running. Each scene is held until the next
scene's start time from the script's timing block, so the footage lines up with the narration.

    python scripts/record_demo.py           # replay mode: recorded Ring sandbox captures (repeatable)
    python scripts/record_demo.py --live    # live WHEP captures; prompts you to click the Playground
    python scripts/record_demo.py --live --scene package   # only the package scene, live, with the
                                            # Ring Playground on screen (then scripts/splice_live_scene.py)

Outputs video/raw/demo_raw.webm (1920x1080) and video/timeline.json (actual scene start times).
Then run scripts/make_video.py to cut, caption and add the placeholder voiceover.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "http://localhost:8000"
WEB = "http://localhost:5180"
SCENES_DIR = ROOT / "video" / "scenes"
RAW_DIR = ROOT / "video" / "raw"


def load_scenes() -> list[dict]:
    text = (ROOT / "docs" / "VIDEO_SCRIPT.md").read_text()
    block = re.search(r"## Timing data\s*```json\s*(.*?)```", text, re.S)
    if not block:
        sys.exit("docs/VIDEO_SCRIPT.md has no ```json timing block")
    return json.loads(block.group(1))


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def check_services(live: bool) -> None:
    for url in (API + "/health", WEB + "/"):
        try:
            urllib.request.urlopen(url, timeout=5)
        except OSError:
            sys.exit(f"{url} is not reachable; start the backend (uvicorn) and the web app (npm run dev)")
    if live:
        from backend.config import current_access_token
        import base64

        token = current_access_token()
        try:
            exp = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["exp"]
        except (IndexError, ValueError, KeyError):
            exp = 0
        if exp - time.time() < 10 * 60:
            sys.exit("The sandbox token in .env expires in under 10 minutes (or is unreadable). "
                     "Generate a new one in the Ring developer console and update RING_ACCESS_TOKEN.")


def warm_up() -> None:
    """One throwaway replay so the server's DynamoDB/S3 connections are warm before recording.

    Uses the motion clip (bird feeder): no package, normal hour, so nothing is sent to anyone.
    """
    started = time.monotonic()
    post("/demo/clock", {"set": datetime.now().replace(hour=12, minute=0).strftime("%Y-%m-%dT%H:%M")})
    post("/demo/replay", {"event_type": "motion"})
    print(f"warm-up replay: {time.monotonic() - started:.1f}s")


def reset_state(day: datetime) -> None:
    """Empty events/packages/notifications (linked accounts are kept) and set the demo clock."""
    from backend.store import store_from_env

    store_from_env().clear()
    post("/demo/clock", {"set": day.replace(hour=14, minute=9, second=55).strftime("%Y-%m-%dT%H:%M:%S")})


class Recorder:
    def __init__(self, page, scenes: list[dict], live: bool):
        self.page, self.live = page, live
        self.scenes = {s["id"]: s for s in scenes}
        self.page_start = time.monotonic()  # Playwright's video starts with the page
        self.t0: float | None = None
        self.timeline: dict[str, float] = {}

    def begin(self, scene_id: str) -> None:
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        self.timeline[scene_id] = round(now - self.page_start, 3)
        print(f"[{now - self.t0:6.1f}s] {scene_id}")

    def at(self, scene_id: str, offset: float) -> None:
        """Sleep until <scene start + offset> on the script's clock."""
        target = self.t0 + self.scenes[scene_id]["start"] + offset
        time.sleep(max(0.0, target - time.monotonic()))

    def elapsed(self, scene_id: str) -> float:
        return time.monotonic() - (self.t0 + self.scenes[scene_id]["start"])

    def spread(self, scene_id: str, steps: list) -> None:
        """Give each step an equal, fixed slot of the scene's remaining time (absolute slot starts,
        so a slow page load shortens its own slot instead of pushing later steps)."""
        scene = self.scenes[scene_id]
        start = self.elapsed(scene_id)
        each = (scene["end"] - scene["start"] - start) / len(steps)
        for i, step in enumerate(steps):
            self.at(scene_id, start + i * each)
            step()

    def until_end(self, scene_id: str) -> None:
        target = self.t0 + self.scenes[scene_id]["end"]
        time.sleep(max(0.0, target - time.monotonic()))

    def goto(self, url: str) -> None:
        # Not "networkidle": the app polls /state every 3 s, so the network is rarely idle.
        self.page.goto(url, wait_until="domcontentloaded")
        if url.startswith(WEB):
            self.page.wait_for_selector("main h2", timeout=15_000)
            time.sleep(0.3)
        else:
            self.page.wait_for_load_state("load")

    def caregiver(self) -> None:
        self.goto(f"{WEB}/caregiver" + ("" if self.live else "?replay=1"))
        self.page.wait_for_selector("text=Demo controls")

    def simulate(self, kind: str) -> None:
        if self.live:
            input(f"\n>>> Click {kind.capitalize()} in the Ring Playground now, then press Enter here… ")
            time.sleep(3)  # let the sandbox switch clips (avoids the first-capture stall)
        started = time.monotonic()
        self.page.get_by_role("button", name=f"Simulate {kind}").click()
        # wait for the agent to finish (status line changes from "…ing")
        self.page.wait_for_function(
            "() => { const s = document.querySelector('.status'); return s && s.textContent && !s.textContent.endsWith('…'); }",
            timeout=120_000)
        time.sleep(0.6)  # the caregiver view refreshes /state right after the action
        print(f"           simulate {kind}: {time.monotonic() - started:.1f}s")

    def set_clock(self, when: datetime) -> None:
        self.page.locator('input[type="datetime-local"]').fill(when.strftime("%Y-%m-%dT%H:%M"))
        self.page.get_by_role("button", name="Set", exact=True).click()
        self.page.wait_for_function("() => (document.querySelector('.status')||{}).textContent?.startsWith('Demo clock now')")

    def expand_activity(self, event_type: str, contains: str | None = None) -> None:
        rows = self.page.locator("section.activity tbody tr", has_text=event_type)
        row = rows.filter(has_text=contains).first if contains else rows.first
        row.scroll_into_view_if_needed()
        row.locator("summary").click()
        row.scroll_into_view_if_needed()


def run(live: bool) -> None:
    from playwright.sync_api import sync_playwright

    scenes = load_scenes()
    check_services(live)
    today = datetime.now()
    warm_up()
    reset_state(today)
    tomorrow = today + timedelta(days=1)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for old in RAW_DIR.glob("*.webm"):
        old.unlink()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        # A true 1920x1080 viewport with 150% zoom: the layout of a 1280x720 screen, rendered at full resolution.
        context = browser.new_context(viewport={"width": 1920, "height": 1080},
                                      record_video_dir=str(RAW_DIR), record_video_size={"width": 1920, "height": 1080})
        context.add_init_script("document.addEventListener('DOMContentLoaded', () => {"
                                " if (window.top === window) document.documentElement.style.zoom = '1.5'; });")
        page = context.new_page()
        r = Recorder(page, scenes, live)

        # 0:00 the problem
        r.goto((SCENES_DIR / "title.html").as_uri())
        time.sleep(1.0)  # let the first frame settle before the clock starts
        r.begin("problem")
        r.until_end("problem")

        # 0:15 what it is
        r.begin("what")
        r.goto((SCENES_DIR / "split.html").as_uri())
        r.until_end("what")

        # 0:30 a package arrives
        r.begin("package")
        r.caregiver()
        r.at("package", 1)
        r.simulate("package")  # ~7-12 s: agent, analysis on file, DynamoDB, S3 snapshot upload
        r.at("package", 3)

        def reasoning() -> None:
            r.caregiver()
            r.expand_activity("package")

        r.spread("package", [lambda: r.goto((SCENES_DIR / "yolo.html").as_uri()),
                             lambda: r.goto(f"{WEB}/resident"),
                             reasoning])
        r.until_end("package")

        # 1:05 reminder and pickup
        r.begin("pickup")
        page.get_by_role("button", name="+3 h").click()
        r.at("pickup", 5)
        r.goto(f"{WEB}/resident")
        r.at("pickup", 11)
        page.get_by_role("button", name="I picked up the package").click()
        r.until_end("pickup")

        # 1:25 the 3 AM car
        r.begin("night")
        r.caregiver()
        r.set_clock(tomorrow.replace(hour=3, minute=0))
        r.at("night", 3)
        r.simulate("vehicle")
        page.locator("section.alerts").scroll_into_view_if_needed()
        # Set up the next scene off-screen while the alert stays in view: a new package at 9 AM.
        # (Each replay takes ~10 s against DynamoDB/S3 in us-east-1; two in one 26 s scene don't fit.)
        def next_scene_setup() -> None:
            post("/demo/clock", {"set": tomorrow.replace(hour=9, minute=0).strftime("%Y-%m-%dT%H:%M")})
            post("/demo/replay", {"event_type": "package"})
        setup = threading.Thread(target=next_scene_setup)
        r.at("night", 18)
        setup.start()
        r.until_end("night")

        # 1:49 designed not to cry wolf (a package is now waiting from the setup above)
        r.begin("nofalse")
        setup.join(timeout=60)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1.0)
        r.simulate("vehicle")
        r.expand_activity("vehicle", "different view")
        r.until_end("nofalse")

        # 2:10 how it's built
        r.begin("built")
        r.goto((SCENES_DIR / "architecture.html").as_uri())
        r.until_end("built")

        # 2:35 honest close
        r.begin("close")
        r.goto((SCENES_DIR / "close.html").as_uri())
        r.until_end("close")
        time.sleep(0.5)

        video = page.video
        context.close()
        browser.close()
        raw = Path(video.path())
        target = RAW_DIR / "demo_raw.webm"
        raw.rename(target)

    (ROOT / "video" / "timeline.json").write_text(json.dumps(
        {"mode": "live" if live else "replay", "scene_offsets_s": r.timeline}, indent=2))
    print(f"\nraw video: {target}\ntimeline:  video/timeline.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="trigger live WHEP captures (needs a fresh sandbox token)")
    parser.add_argument("--scene", choices=["package"], help="record only this scene (with --live: Ring Playground "
                        "click + live capture, for scripts/splice_live_scene.py)")
    args = parser.parse_args()
    if args.scene == "package" and args.live:
        from scripts.record_live_scene import run as run_live_package

        run_live_package()
    elif args.scene:
        sys.exit("--scene package needs --live")
    else:
        run(args.live)
