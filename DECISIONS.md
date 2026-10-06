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
