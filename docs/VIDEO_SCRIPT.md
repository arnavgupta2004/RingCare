# DoorSight demo video — script (2:50)

Narration and on-screen actions with timestamps. This file is the single source of timing:
- `scripts/record_demo.py` reads the JSON block at the bottom to drive the recording.
- `scripts/make_video.py` reads it to build `captions/demo.srt`, the burned-in captions and the placeholder voiceover.

Target 2:50, safely under the 3:00 limit.

| Time | Scene | On screen | Narration |
|-|-|-|-|
| 0:00–0:15 | The problem | Title card | "For an older or low-vision person living alone, a video doorbell isn't much help: it shows a picture they can't easily see. Did a parcel arrive? Is it still outside? Who's in the driveway at 3 AM? DoorSight answers those questions." |
| 0:15–0:29 | What it is | Resident and caregiver views side by side | "DoorSight is a Ring-powered doorstep assistant. It watches Ring live video, understands what's at the door, and sends calm, accessible updates to the resident and alerts to a caregiver." |
| 0:29–1:04 | A package arrives | Caregiver view → **Simulate package** → YOLO-World frame with the box around the parcel → resident view ("A package was left at your door…") → caregiver view, agent reasoning expanded | "Frames come from Ring's live WebRTC stream. Open-vocabulary detection finds the parcel locally, and a Strands agent decides who to tell and why. Every step is recorded." |
| 1:04–1:24 | Reminder and pickup | Caregiver **+3 h** → resident view shows the gentle reminder → press **I picked up the package** | "One big button. No menus, nothing to read closely." |
| 1:24–1:49 | The 3 AM car | Set the clock to 3 AM → **Simulate vehicle** → caregiver alert with score, explanation and "emailed" | "A car at 3 AM is unusual for this home, based on its own visit pattern, so the caregiver gets an alert that explains why." |
| 1:49–2:15 | Designed not to cry wolf | New package at 9 AM → **Simulate vehicle** while it's open → "different view — can't verify package" | "A naive system would say the parcel was stolen just because a different camera saw no box. DoorSight fingerprints the camera view and refuses to raise a false alarm. Its safety rules are enforced in the agent's tools, not just its prompt." |
| 2:15–2:36 | How it's built | Architecture diagram | "Ring WHEP video and webhooks, YOLO-World locally, a Strands agent on Amazon Bedrock, DynamoDB for state, a private S3 bucket for snapshots, SNS for caregiver email, all under a least-privilege IAM user, with more than 260 tests." |
| 2:36–2:50 | Honest close | Closing card | "Ring's sandbox has one doorbell and no webhooks, so we built around what's real and logged twelve friction entries for the Ring team. DoorSight helps people stay independent at their own front door." |

## Notes for recording

- **Final cut:** the package scene (0:29–1:05) is **live**:
  1. Your screen recording of the Ring Playground (cropped to the Package button and the Live Stream dialog, so the token on the page never shows).
  2. A real WHEP capture through DoorSight (`record_demo.py --live --scene package --no-console`).
  3. YOLO-World on the fresh frames, the resident view, and the agent's reasoning.

  It carries a "LIVE from Ring sandbox" badge and an on-screen note saying how many seconds of capture wait were cut. `scripts/splice_live_scene.py` puts it in place of the replayed scene; build the final files with `make_video.py --raw video/raw/demo_composite.mp4 --timeline video/timeline_composite.json`. The other scenes replay recorded sandbox captures.

- **Automated draft:** `scripts/record_demo.py` replays the real Ring sandbox captures already on disk (frames recorded earlier from the Playground's WHEP stream) through the live server and agent. It is repeatable, and the Ring console doesn't need to be open.
- **Live version (`--live`):** triggers real WHEP captures through `/simulate-event`. Click the matching Playground button a few seconds before each trigger; the script pauses and prompts you. Needs a fresh sandbox token.
- **Email footage:** the 3 AM scene shows "emailed" on the alert. If you film the SNS email arriving on your phone, cut it in at about 1:40 (the alert appears around 1:37).
- **Bedrock:** the narration says "a Strands agent on Amazon Bedrock". Until Bedrock is unlocked, the on-screen agent is the rules brain (labelled RULES). If it's still locked when you record the final take, consider "a Strands agent, built for Amazon Bedrock" instead.
- **No music.** Captions are burned in, since judges often watch muted.

## Timing data

```json
[
  {"id": "problem",   "start": 0,   "end": 15,  "narration": "For an older or low-vision person living alone, a video doorbell isn't much help: it shows a picture they can't easily see. Did a parcel arrive? Is it still outside? Who's in the driveway at 3 AM? DoorSight answers those questions."},
  {"id": "what",      "start": 15,  "end": 29,  "narration": "DoorSight is a Ring-powered doorstep assistant. It watches Ring live video, understands what's at the door, and sends calm, accessible updates to the resident and alerts to a caregiver."},
  {"id": "package",   "start": 29,  "end": 64,  "narration": "Frames come from Ring's live WebRTC stream. Open-vocabulary detection finds the parcel locally, and a Strands agent decides who to tell and why. Every step is recorded."},
  {"id": "pickup",    "start": 64,  "end": 84,  "narration": "One big button. No menus, nothing to read closely."},
  {"id": "night",     "start": 84,  "end": 109, "narration": "A car at 3 AM is unusual for this home, based on its own visit pattern, so the caregiver gets an alert that explains why."},
  {"id": "nofalse",   "start": 109, "end": 135, "narration": "A naive system would say the parcel was stolen just because a different camera saw no box. DoorSight fingerprints the camera view and refuses to raise a false alarm. Its safety rules are enforced in the agent's tools, not just its prompt."},
  {"id": "built",     "start": 135, "end": 156, "narration": "Ring WHEP video and webhooks, YOLO-World locally, a Strands agent on Amazon Bedrock, DynamoDB for state, a private S3 bucket for snapshots, SNS for caregiver email, all under a least-privilege IAM user, with more than 260 tests."},
  {"id": "close",     "start": 156, "end": 170, "narration": "Ring's sandbox has one doorbell and no webhooks, so we built around what's real and logged twelve friction entries for the Ring team. DoorSight helps people stay independent at their own front door."}
]
```
