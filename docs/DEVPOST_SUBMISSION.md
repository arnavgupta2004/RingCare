# Devpost submission — ready to paste

Everything for the Devpost form, field by field. Items marked **[YOU]** still need you.

---

## Project name

DoorSight

## Elevator pitch (≤ 200 characters)

A Ring doorstep assistant for older and low-vision residents: it watches live video, explains what's at the door in calm, large-text updates, and alerts a caregiver only when it matters.

*(186 characters)*

## Video

**[YOU]** YouTube URL, public, in English. Upload `video/demo_tts.mp4` (placeholder voice) or your re-voiced version. Put the CC BY clip credits (below) in the video description.

## Try it out

- Code: https://github.com/arnavgupta2004/RingCare
- Demo video: **[YOU]** YouTube link

## Track and challenges

- **Track:** Ring
- **Mini challenge:** AWS Builder
- **Did this project exist before the hackathon?** No. It was built during the hackathon window; the first commit is the project skeleton.

## Built with

`python` `fastapi` `ring-api` `webrtc` `whep` `aiortc` `yolo-world` `ultralytics` `opencv` `strands-agents` `amazon-bedrock` `amazon-dynamodb` `amazon-s3` `amazon-sns` `aws-iam` `boto3` `react` `vite` `typescript` `playwright` `axe-core` `pytest` `moto` `ngrok`

---

## About the project

### Inspiration

A video doorbell assumes you can see the video. For an older or low-vision person living alone, the questions are simpler and harder: did a parcel arrive, is it still outside, who is in the driveway at 3 AM? And the family member who looks out for them doesn't want every motion alert; they want to know when something is actually wrong.

**[YOU, optional]** One or two sentences about your personal motivation, if you have one.

### What it does

Every Ring event (package, vehicle, motion) goes to an agent. It opens the doorbell's live video, works out what is at the door, and remembers what happened before.

- **For the resident:** a large-text, high-contrast, screen-reader-first view. "A package was left at your door at 2:10 PM", a gentle reminder three hours later, and one big "I picked up the package" button.
- **For the caregiver:** short, evidence-based alerts by email:
  - **Possible missing package:** only when the *same camera view* shows the package gone and nobody marked it picked up.
  - **Unusual-hour activity:** a car at 3 AM, scored against the home's own visit pattern.
  - **Daily digest:** a summary instead of a stream of notifications.
- **Every alert shows why:** the score, the camera-view check, and each tool call the agent made. Every message is labelled with where its information came from (vision model, automatic estimate, or rule).

### How I built it

- **Ring:**
  - **Device discovery:** `GET /v1/devices`.
  - **Live video:** WHEP sessions captured with aiortc (video-only, 1 frame/s, always closed cleanly).
  - **Webhooks:** HMAC-signed, verified over the raw body.
  - **One-way account linking:** sign-in on the Account Link URL, nonce matching, App-Integrations POST + PATCH, and refresh tokens that rotate.
- **Vision:** YOLO-World open-vocabulary detection runs locally and finds the "cardboard box" a standard COCO model can't. Claude Haiku 4.5 on Amazon Bedrock describes the scene as validated strict JSON.
- **Agent:** a Strands agent with eight tools: capture, describe, package state/update, visit baseline, notify resident/caregiver, daily digest.
  - **Safety rules live in the tools, not just the prompt.** A capture from a different camera can never mark a package missing; DoorSight fingerprints each camera view with a perceptual hash and histogram, with thresholds measured on real captures.
  - **Fallback:** if the model fails, a deterministic rules brain finishes the event through the same tools.
- **AWS:**
  - **Data:** DynamoDB (state), S3 (private snapshots, presigned URLs), SNS (caregiver email).
  - **Security:** a least-privilege IAM user, so the server never runs with root credentials.
- **Web:** React + Vite, with 0 axe-core accessibility violations (WCAG 2.2 A/AA).
- **Tests:** 262 pytest tests, including:
  - **Both stores:** store tests against SQLite and DynamoDB (moto).
  - **Agent:** the agent loop driven by a scripted fake model.
  - **Account linking:** tested against a fake Ring OAuth/API server.

### Challenges I ran into

- **No live webhooks:** the Ring Playground's buttons switch the video clip but send no webhooks. I added `/simulate-event`, which runs the same payload through the same handler.
- **Linking needs a paid plan:** live account linking stops at the Connect step without a Ring Protect plan, so I verified the full flow against a fake Ring server instead.
- **Bedrock blocked:** my AWS account still can't invoke any Bedrock model (support case open). So the agent runs on its rules brain, and switches to Haiku automatically once `check_bedrock.sh` passes.
- **Short sandbox streams:** WHEP streams sometimes end after 10–15 s with no signal, which needed careful capture logic.
- **A false alarm by design:** while building the package logic, I found that a capture from a different camera would report a delivered package as missing. That led to camera-view fingerprinting.

### Accomplishments that I'm proud of

- **Safety enforced in code:** the safety rules live in the agent's tools, and refusals reach the model as errors.
- **Honest labelling:** every alert explains itself, and the UI never passes off an estimate as a vision-model answer.
- **Accessibility:** a resident view built for low-vision users, with 0 axe violations.
- **Least privilege:** a scoped IAM user, with denials verified.
- **Friction log:** 12 entries with reproduction steps for the Ring team.

### What I learned

- How Ring's one-way account linking works (nonces bound to the account ID, the mandatory PATCH, why one-way apps can't DELETE).
- Why DynamoDB GSIs are the wrong tool for read-your-writes state.
- How much it helps to design an agent so it can be tested without its model.

### What's next for DoorSight

- **Live Bedrock:** switch to Bedrock (Haiku 4.5) as soon as account access is granted.
- **Live linking:** verify account linking against a real Ring Protect account.
- **Sensors:** add contact sensors, once Ring's sandbox offers them, for a fuller picture of the home.
- **Daily rhythm:** a scheduled daily digest, and caregiver settings for quiet hours.

---

## AWS Builder: AWS services used and how

- **Amazon Bedrock:** Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) in two roles:
  - **Vision:** describes three representative doorbell frames per event as strict, validated JSON, including one calm sentence for the resident (Converse API).
  - **Agent brain:** chooses tools and writes messages (Strands `BedrockModel`).
  - **Status:** code complete. My account can't invoke models yet (support case open); a labelled stub and the rules brain run meanwhile, and the switch is automatic.
- **Strands Agents:** the agent loop. Eight tools, a timeout, and fallback to a deterministic brain; every tool call and the reason are stored and shown to the caregiver.
- **Amazon DynamoDB:** the state store (events, packages, notifications, linked Ring-account tokens). One on-demand table designed for strongly consistent reads.
- **Amazon S3:** event snapshots in a private bucket (public access blocked, SSE-S3, 30-day lifecycle). The web app only gets 1-hour presigned URLs.
- **Amazon SNS:** caregiver alert emails (possible missing package, unusual-hour activity) and the daily digest.
- **AWS IAM:** the server runs as a least-privilege user that can only reach the DoorSight table, the snapshot objects, the caregiver topic and the two Bedrock models. Listing, deleting and IAM actions are denied (verified).

## Product feedback (per tool)

Paste each tool's section from [docs/PRODUCT_FEEDBACK.md](PRODUCT_FEEDBACK.md). It already follows Devpost's five questions (tools used, what worked, what needs work, onboarding, would you build again). **[YOU]** Confirm the question-5 answers marked *(draft — Arnav to confirm)*.

## Feature requests

1. **Critical:** Playground actions deliver real HMAC-signed webhooks to the configured Webhook URL, plus a "Send test webhook" button.
2. **Critical:** let the sandbox/Playground account complete the Connect step, so account linking can be verified before certification.
3. **Important:** document a package/delivery event (or the complete `subType` list), with one canonical v1.1 payload.
4. **Important:** allow `DELETE /v1/accounts/me/app-integrations` for one-way apps, and add per-account disconnect to the private-app Connect step.
5. **Nice-to-have:** simulated sensors and chime, seeded event history, a documented stream length with an end-of-stream signal, and a refreshable sandbox token.

Details and links: [docs/FEATURE_REQUESTS.md](FEATURE_REQUESTS.md).

## Friction log

https://github.com/arnavgupta2004/RingCare/blob/main/FRICTION_LOG.md: 12 entries ordered by severity, each with task, steps, expected vs actual, severity, workaround and suggestion. **[YOU]** Add Ring's exact on-screen message to F12.

## Video description (for YouTube)

> DoorSight: a Ring doorstep assistant for older and low-vision residents and their caregivers. Built for the Amazon Developer Hackathon (Ring track + AWS Builder).
> Code: https://github.com/arnavgupta2004/RingCare
>
> The Ring sandbox video clips shown are stock footage licensed under CC BY 4.0:
> - **[YOU]** Bird feeder clip: title, author, link
> - "Thief stealing our package" by YouTube user frollard, CC BY 4.0, clipped from original. **[YOU]** add the link (shown in the Ring Playground)
> - **[YOU]** Parking-lot vehicle clip: title, author, link
>
> Demo footage replays real Ring sandbox captures through the live DoorSight server; the agent runs on its rules brain while Bedrock access is pending.
