# Decisions

Short record of design choices and known limitations.

## D1. Local detector: YOLO11n with COCO classes (step 3)

**Choice:** `yolo11n.pt` (ultralytics, smallest pretrained model, ~5 MB, CPU-friendly), confidence threshold 0.25. Weights download to `data/models/` (gitignored).

**Class mapping:**

| Group | COCO classes |
|-|-|
| person | `person` |
| vehicle | `car`, `truck`, `bus`, `motorcycle` |
| package | `suitcase`, `handbag`, `backpack` (proxy only) |

**Limitation — packages:** COCO has no "box", "parcel" or "package" class. The proxy classes catch bags and luggage left at a door, not cardboard boxes. On the Ring sandbox package clip, YOLO11n detects **nothing at all**, even at confidence 0.15. The cardboard box on the step is invisible to it.

**Consequence:** YOLO is a fast, cheap signal for people and vehicles (on the sandbox vehicle clip it finds cars in 14 of 16 frames). The package decision comes from the vision-language model in D2. The Bedrock prompt tells it that the detector can't see boxes, so it trusts the images over the detector.

**Options if this needs fixing later:**
- An open-vocabulary detector (e.g. YOLO-World with prompts like "cardboard box", "parcel") — no training, larger model.
- Fine-tune YOLO on a small labelled parcel dataset.

## D2. Scene description: Anthropic Claude on Amazon Bedrock via boto3 Converse (step 3)

**Choice:** `bedrock-runtime` `converse` in `us-east-1`, model from `BEDROCK_MODEL_ID` (default `us.anthropic.claude-opus-5-5`). Three frames per event (first, last, and the frame with the most detections, else the middle), resized to 1024 px wide, plus a compact YOLO summary.

**Output contract:** strict JSON with `scene_summary`, `objects_present`, `package_visible`, `vehicle_visible`, `people_visible`, `confidence`, `accessible_description`. Every field and type is validated. One automatic re-ask on invalid JSON, then the error is recorded in the analysis file instead of crashing the event.

**Why not forced tool use for JSON:** current Claude models reject forced `tool_choice`, so the prompt asks for a bare JSON object and the code validates it.

## D3. Capture retry (step 3 fix)

If a capture returns fewer than 3 frames, wait 3 s and retry once. In testing, the first WHEP session after a Ring Playground button click often stalls within ~1 s while the clip switches. If the retry is worse, the first attempt's frames are kept.
