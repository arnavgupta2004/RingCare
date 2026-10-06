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
| present / reminded | new capture (≥3 frames) **of the arrival view** shows no package, no pickup | missing | caregiver: possible missing package |

- **"Package detected"** means YOLO-World saw the package group in ≥30% of the capture's frames.
- **Reminders are checked** on every event, on every clock change, and by a 60 s background loop (`REMINDER_CHECK_INTERVAL_S`).
- **Missing requires the same view:** see D6.

**Unusual-hour score:** only for vehicle/motion events (`vehicle`, `motion`, `human`, `other_motion`).
- **Baseline:** per hour = prior from `config/visit_profile.json` (quiet 22:00–06:00 at 0.2, otherwise 3.0) + observed events in that hour. The current event is excluded from its own baseline.
- **Score:** `1 − weight(hour) / mean hourly weight`, clipped to [0, 1]. A vehicle at 03:00 on a fresh profile scores 0.90; daytime scores 0.
- **Alert:** at ≥ `unusual_threshold` (0.7), a caregiver notification is queued with the explanation string.

**Notifications** are only queued for now (`status: "queued"`): `{audience, kind, text, event_id, package_id, source}`. `source` is where the observation came from: `bedrock`, `stub`, or `rules` for purely time-based reminders.

## D6. A package can only go missing on a capture of its arrival view

**Problem:** "new capture without a package → missing" is wrong whenever the capture comes from a different view. In the sandbox, the Vehicle clip comes from another camera. In a real multi-camera home, the back-door camera never shows the front-door parcel. Either way, a false "possible missing package" alert would go to the caregiver.

**Choice:** store a compact view fingerprint with each package at arrival (`packages.arrival_view`). Compare every later capture against it (`backend/vision/fingerprint.py`).
- **Fingerprint:** for the first, middle and last frames of a capture, a 63-bit perceptual hash (DCT of a 32×32 grayscale thumbnail) plus a 32-bin grayscale histogram. About 0.5 KB per capture.
- **Match:** if both captures have a `device_id`, they must agree. Then the best frame pair needs hash distance ≤ 12 **and** histogram correlation ≥ 0.7.
- **No match:** the event records `package_check = "different view — can't verify package"` and the package is left exactly as it was. Its state is unchanged, `last_seen` isn't refreshed, and nobody is notified.
- **No reference view** (packages from before this change): "no reference view — can't verify package". These packages are never auto-marked missing.
- **Too-short capture** (< 3 frames) of the right view: "capture too short — can't verify package".

**Measured on real sandbox captures:**

| Comparison | Hash distance | Histogram correlation |
|-|-|-|
| same view, other frames | 0–2 | ≥ 0.996 |
| same view, parcel painted out (OpenCV inpaint) | 4 | 0.996 |
| vehicle-clip views | 32–36 | ≤ 0.18 |
| bird-feeder view | 28–32 | ≤ −0.16 |

Both thresholds sit far from both clusters. The painted-out test matters most, because a real "package taken" capture must still count as the same view.

**Limits:** large lighting changes (day → night, IR mode) shift the histogram and could make the same camera look like a different view. That is the safe direction: no false alarm, but a theft at night might not be confirmed until a daytime capture. A pan/tilt camera or a re-mounted doorbell needs a new reference.

## D7. AWS backends: DynamoDB, S3, SNS (selectable, minimal cost)

**Selection:**

| Setting | Values |
|-|-|
| `STATE_BACKEND` | `sqlite` (default) \| `dynamodb` |
| `SNAPSHOT_BACKEND` | `local` (default) \| `s3` |
| `SNS_TOPIC_ARN` | set → caregiver alerts go out by SNS email; unset → logged to `logs/notifications.log` |

`scripts/aws_setup.sh` creates everything idempotently and records the names in `.env`; `--enable` switches state and snapshots to AWS. `scripts/aws_teardown.sh` deletes it all after a typed confirmation (`--dry-run` to preview).

**DynamoDB:** one on-demand table, no secondary indexes.
- **Why no indexes:** GSIs are only eventually consistent, but the state machine reads its own writes immediately (create a package, then look up the open package). So each entity kind is one partition (`pk = EVENT | PACKAGE | NOTIFICATION`) with a time-ordered sort key, read with `ConsistentRead`.
- **Lookups by id:** a small pointer item, `ID#<kind>#<id>`.
- **Scale:** one home produces tens of items a day, far below per-partition limits. A multi-home version would put the home id in `pk`.
- **Tests:** the same store contract and doorstep tests run against SQLite and DynamoDB (moto) through one parametrized fixture.

**S3 snapshots:**
- **What's uploaded:** per event, the representative frame (most detections) plus the first and last frames, under the same `frames/...` key as on disk.
- **Bucket:** private (all public-access blocks on), SSE-S3, and a lifecycle rule that expires frames after 30 days.
- **UI access:** only 1-hour presigned GET URLs.
- **Upload failure:** the event keeps its local reference, so the UI still shows a picture.

**SNS:** caregiver notifications are published to `doorsight-caregiver-alerts`.
- **Status:** each notification records `status = sent | logged | failed` and the SNS message id.
- **Failures:** a failed publish falls back to the log file.
- **Resident messages** stay in the resident web view and are not emailed.

**Cost:** no always-on resources. On-demand DynamoDB, pay-per-request S3 and SNS (email: first 1,000/month free). The demo story costs well under a cent.

**Verified on this account (6 Oct 2026):**
- **DynamoDB:** create table, read/write.
- **S3:** create bucket; unsigned GET → 403, presigned GET → 200.
- **SNS:** create topic, subscribe, publish.
- **Bedrock:** still blocked at the account level (see D4).

## D8. The agent (step 5): Strands tools, two brains, guard rails in code

**Shape:** every event (webhook or `/simulate-event`) goes to `AgentRunner.handle_event`. A *brain* decides which of eight tools to call:

| Tool | What it does |
|-|-|
| `start_live_capture` | live WHEP capture, or an existing capture |
| `describe_scene` | YOLO-World plus a Bedrock or stub description |
| `get_package_state` | open package, view check, recommended and allowed actions |
| `update_package_state` | apply an allowed action |
| `get_visit_baseline` | unusual-hour score |
| `notify_resident`, `notify_caregiver` | send a message |
| `write_daily_digest` | write and send the caregiver digest |

Every tool call (input, output or error, brain, time) and the brain's stated reason are stored on the event (`agent_trace`, `agent_reason`, `agent_brain`). The caregiver view shows them.

**Brains:**
- `rules`: deterministic. Calls the same tools in the same order as the original doorstep logic. Tests check that it produces identical packages, notifications and event checks to `Doorstep.record_event` across five scenarios, on SQLite and DynamoDB. Its messages are labelled `source: rules`.
- `bedrock`: a Strands `Agent` on `BedrockModel` (`AGENT_MODEL_ID`, default `us.anthropic.claude-haiku-4-5-20251001-v1:0`), with the system prompt in `backend/agent/prompts.py`. Its messages are labelled `source: bedrock`.
- **Selection:** `AGENT_BRAIN=rules|bedrock|auto`. `auto` (the default) runs `scripts/check_bedrock.sh` at server startup, in a background thread so startup isn't delayed, and picks `bedrock` only if it passes. Until the check finishes, and whenever it fails, events use `rules`. When Bedrock access is granted, a restart switches over with no code change.

**Guard rails are in the tools, not the prompt:**
- `get_package_state` returns the allowed actions, and `update_package_state` refuses anything else. For example, a capture from a different camera view only allows `no_change`.
- `notify_resident(kind=package_arrived)` needs a package created in this event.
- `notify_caregiver(package_missing)` needs the package marked missing in this event.
- `notify_caregiver(unusual_hour)` needs `get_visit_baseline` first.
- Text must be non-empty and at most 600 characters.
- Refusals come back to the model as tool errors (Strands `status: error`), and nothing changes.

**Failure handling:**
- If the model errors or times out (`AGENT_TIMEOUT_S`, default 120 s), the rules brain finishes the event from where it stopped.
- Tools are idempotent per event: capture and describe are cached, the same audience and kind is never notified twice, and a package action is applied once. So a fallback can't double-capture or double-alert.
- The reason is prefixed with `[bedrock agent failed (<error>); handled by rules]`.
- The event is always saved, with an unusual-hour score even if the agent never asked for it.

**Daily digest:** the facts (deliveries, pickups, reminders, missing, unusual activity, door events, still at the door) are computed from stored records. A model can only add a one-sentence note, so it can't invent events. The window is the 24 hours ending now (sim), inclusive, or a calendar day. It is sent to the caregiver by SNS and shown in the caregiver view; trigger it with `POST /digest` or "Send daily digest".

**Testing without Bedrock:** `tests/fakes.py` has a scripted Strands `Model` that streams pre-written tool calls through the real Strands agent loop. It covers the happy path, refused actions, guard ordering, model exceptions, timeouts, an agent that does nothing, and digests.

## D9. Real one-way account linking (step 7)

**Flow (from the Ring docs):**
1. Ring POSTs a form-encoded `code` to `/token`. We exchange it at `https://oauth.ring.com/oauth/token` (60 s window), call `GET /v1/users/me` for the Ring account ID, and store the tokens in the configured StateStore as an **unclaimed** linked account. Unclaimed accounts are matchable for 15 minutes.
2. Ring redirects the user to `/link?nonce&time`. We reject `time` older than 600 s or in the future, then **require sign-in** before touching the nonce.
3. After sign-in we match the nonce in constant time against unclaimed accounts: `Base64URL_NoPadding(HMAC-SHA256(key, "<time>:<account_id>"))`.
4. `POST /v1/accounts/me/app-integrations {nonce, account_identifier}` (status `awaiting`), then the mandatory `PATCH {status: completed}`. If the PATCH fails, the account is still linked and `/home` offers "Finish setup".

**Sign-in:** one local demo user (`DEMO_USER_EMAIL`).
- **Password:** stored only as a scrypt hash (`DEMO_USER_PASSWORD_HASH`, set by `scripts/set_demo_password.py`).
- **Session cookie:** HMAC-signed (`SESSION_SECRET`), HttpOnly and SameSite=Lax, valid 8 h. It's Secure over HTTPS (ngrok).
- **Protections:** POSTs are refused when Origin doesn't match the host. Sign-in is throttled to 5 failures per 5 minutes per client.
- **Ring confirmation:** the masked email (`d***o@doorsight.local`) is sent as `account_identifier`, which Ring shows in its confirmation email.

**Tokens:**
- **Storage:** stored per Ring account (`LinkedAccount`) in SQLite or DynamoDB. Never logged or rendered: logs name the source as `linked:…<last 6 of account id>` or `sandbox`. Every API request logs which source it used.
- **Refresh:** 5 minutes before expiry, once on a 401 (then retried once), and from a background check every 10 minutes that also refreshes anything not refreshed in 24 h, so the ~30-day refresh token never lapses. Refresh tokens rotate.
- **Failed refresh:** a refresh rejected with 400/401 marks the account `needs_relink` and calls fall back to the sandbox token.
- **Selection:** API calls (device list, WHEP capture) use the most recently linked account's token, else `RING_ACCESS_TOKEN`.

**Disconnect:** `DELETE app-integrations` is not available to one-way apps (403; friction F11). So Disconnect pauses the integration (`PATCH awaiting`), deletes our tokens and tells the user to remove DoorSight in the Ring app for a full revoke. An `app_integration_removed` webhook deletes the tokens too. Lifecycle webhooks no longer go to the door agent.
