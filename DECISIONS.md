# Decisions

Short record of design choices and known limitations.

## D1. Local detector: YOLO-World, open-vocabulary (step 3)

**History:** started with `yolo11n.pt` (COCO). COCO has no box/parcel class, and on the Ring sandbox package clip it detected **nothing**, even at confidence 0.15. Bag classes (suitcase, handbag, backpack) as a stand-in didn't help.

**Choice:** `yolov8s-worldv2.pt` (ultralytics YOLO-World, ~25 MB) prompted with custom classes, confidence threshold 0.25. Weights download to `data/models/` (gitignored). It needs OpenAI CLIP (`clip` from `ultralytics/CLIP`, pinned in requirements). The CLIP text encoder (~340 MB) downloads to `weights/clip/` (gitignored) on first use.

| Group | Prompt classes |
|-|-|
| package | `cardboard box`, `package`, `parcel` |
| person | `person` |
| vehicle | `car`, `truck`, `van` |

**Results on the sandbox captures:**
- **Package clip:** `cardboard box` in 19 of 20 frames, confidence 0.33–0.53, box correctly on the parcel on the step. Only the first, blurrier frame is missed.
- **Vehicle clip:** cars or vans in 15 of 16 frames (best 0.87). The "van" is actually a white pickup.

**Known false positives:** low-confidence (0.26–0.35) `person` hits on a mailbox post in 2 of 16 vehicle frames. Downstream logic should require a detection across several frames, not a single hit. The stub description (D4) requires ≥30% of frames.

**Trade-offs:** YOLO-World is ~4× larger and slower than YOLO11n, and its confidences are lower and less calibrated (a true box scores ~0.5). Fine-tuning on parcel images is the next step if accuracy matters.

## D2. Scene description: Anthropic Claude on Amazon Bedrock via boto3 Converse (step 3)

**Choice:** `bedrock-runtime` `converse` in `us-east-1`, model from `BEDROCK_MODEL_ID`, default `us.anthropic.claude-haiku-4-5-20251001-v1:0`. Describing a few frames doesn't need the largest model; Haiku is much cheaper and faster per event. Any Converse-capable vision model works by changing the env var. Three frames per event (first, last, and the frame with the most detections, else the middle), resized to 1024 px wide, plus a compact YOLO summary.

**Output contract:** strict JSON with `scene_summary`, `objects_present`, `package_visible`, `vehicle_visible`, `people_visible`, `confidence`, `accessible_description`. Every field and type is validated. One automatic re-ask on invalid JSON, then the error is recorded in the analysis file instead of crashing the event.

**Why not forced tool use for JSON:** current Claude models reject forced `tool_choice`, so the prompt asks for a bare JSON object and the code validates it.

## D3. Capture retry (step 3 fix)

If a capture returns fewer than 3 frames, wait 3 s and retry once. In testing, the first WHEP session after a Ring Playground button click often stalls within ~1 s while the clip switches. If the retry is worse, the first attempt's frames are kept.

## D4. Stub description when Bedrock is unavailable

**Context:** this AWS account currently can't invoke any Bedrock model ("Operation not allowed" for every model, including Amazon Nova, in every region). `scripts/check_bedrock.sh` tells an account-level block apart from a model-level one.

**Choice:** if the Bedrock call fails, `analyze_event` writes a detector-only description with the same result fields and marks it clearly:
- `description.source = "stub"` (and `"bedrock"` for real answers), copied to top-level `description_source` and to `analysis.source` in `logs/events.jsonl`.
- `description.reason` holds the Bedrock error.
- A group is "visible" only if detected in ≥30% of frames.
- Confidence is capped at 0.6.
- Accessible sentences come from fixed calm templates.

**UI rule (step 6):** anything with `source: "stub"` must be labelled as an automatic estimate, not a vision-model description.

## D5. Doorstep state, demo clock and rules (step 4)

**Demo clock** (`backend/clock.py`): simulated now = real time + offset, in the home timezone (`HOME_TZ`, default `Asia/Kolkata`). The offset comes from `DEMO_TIME_OFFSET` (`3h`, `-90m`, `1d` or seconds) and can be changed with `POST /demo/clock` (`set`, `advance_hours`, `advance_seconds`, `reset`). All time-based rules read this clock, and every event stores both `real_ts` and `sim_ts`.

**Store:** a `StateStore` interface with a SQLite implementation (`data/doorsight.db`; the demo uses `data/demo.db`). Tables: `events`, `packages`, `notifications`. A DynamoDB adapter only needs to implement the same interface.

**Package lifecycle:** one open package at a time.

| From | Trigger | To | Notification |
|-|-|-|-|
| — | package event, package detected, no open package | present | resident: arrival |
| present | `REMINDER_HOURS` (default 3, sim time) elapsed | reminded | resident: gentle reminder |
| present / reminded | `POST /packages/{id}/picked-up` | picked_up | — |
| present / reminded | new capture (≥3 frames) shows no package, no pickup | missing | caregiver: possible missing package |

- **"Package detected"** means YOLO-World saw the package group in ≥30% of the capture's frames.
- **Reminders are checked** on every event, on every clock change, and by a 60 s background loop (`REMINDER_CHECK_INTERVAL_S`).
- **Known caveat:** any valid capture without a package can mark an open package missing. In the sandbox, the vehicle clip comes from a different camera, so a vehicle event while a package is open would wrongly mark it missing. On a real doorbell the view is fixed, so this doesn't arise.

**Unusual-hour score:** only for vehicle/motion events (`vehicle`, `motion`, `human`, `other_motion`).
- **Baseline:** per hour = prior from `config/visit_profile.json` (quiet 22:00–06:00 at 0.2, otherwise 3.0) + observed events in that hour. The current event is excluded from its own baseline.
- **Score:** `1 − weight(hour) / mean hourly weight`, clipped to [0, 1]. A vehicle at 03:00 on a fresh profile scores 0.90; daytime scores 0.
- **Alert:** at ≥ `unusual_threshold` (0.7), a caregiver notification is queued with the explanation string.

**Notifications** are only queued for now (`status: "queued"`): `{audience, kind, text, event_id, package_id, source}`. `source` is where the observation came from: `bedrock`, `stub`, or `rules` for purely time-based reminders.
