# Devpost submission — ready to paste

Everything for the Devpost form, field by field. Items marked **[YOU]** still need you.

---

## Form answers, in the order Devpost asks

### Which AI tools have you leveraged while working on this project?

**[YOU]** Answer this accurately in the form.

**AI inside the product:**
- **YOLO-World:** open-vocabulary detection, run locally.
- **Claude Haiku 4.5 on Amazon Bedrock:** through the Strands Agents SDK; code complete, pending account access.
- **Ring Appstore MCP docs server:** used during development.

### Q1. Which developer tools, APIs and SDKs did you use and for what?

- **Ring APIs (Amazon Vision API):**
  - **Device discovery:** `GET /v1/devices`.
  - **WHEP live video:** a ~20 s capture per event.
  - **Webhooks:** HMAC-signed events.
  - **One-way account linking:** Token Exchange URL, nonce, App-Integrations POST/PATCH, refresh tokens.
- **Ring Appstore MCP:** every API detail during development.
- **Ring Playground:** the sandbox doorbell and its Package / Vehicle / Motion clips.
- **Amazon Bedrock:** Claude Haiku 4.5 (Converse) for vision and the agent brain.
- **Strands Agents SDK:** the agent loop with 8 tools and a rules fallback.
- **Amazon DynamoDB:** state (events, packages, notifications, tokens).
- **Amazon S3:** private snapshots with presigned URLs.
- **Amazon SNS:** caregiver email alerts and the daily digest.
- **AWS IAM:** a least-privilege user for the server.
- **Also used:** FastAPI, aiortc (WebRTC), YOLO-World (ultralytics), OpenCV, React + Vite, Playwright + axe-core, pytest + moto, ngrok.

### Q2. What worked well? / Q3. What needs work?

Paste each tool's section from [PRODUCT_FEEDBACK.md](PRODUCT_FEEDBACK.md) (answers 2 and 3). If the box is short, these one-liners work:

| Tool | Worked well | Needs work |
|-|-|-|
| Ring APIs | Precise docs for WHEP, webhook signing and linking; first device call worked first try | No documented package event; Token Exchange contract unspecified; one-way apps can't DELETE an integration (F5, F6, F11) |
| Ring Appstore MCP | Exact, citable answers from the IDE; caught subtle rules (raw HMAC key, 60 s code window, mandatory PATCH) | Broad queries return nothing; no Playground coverage; IDE panel lacks Claude Code (F3) |
| Ring Playground | Real device and live stream with no hardware; clips switch per button | No webhooks (F9); linking blocked without Ring Protect (F12); streams end early (F10); one device only (F4) |
| Amazon Bedrock | Converse makes models swappable; availability API helped diagnose | Account-wide "Operation not allowed" with no actionable error; streaming needs an extra IAM action |
| Strands Agents | Method tools, errors returned to the model, small `Model` interface easy to fake | `self` naming rule; wrapped exceptions; dependency clash with FastAPI |
| DynamoDB | On-demand, moto tests | GSIs eventually consistent; Decimal/None handling |
| S3 | Presigned URLs, simple hardening | Upload errors aren't `ClientError`; SigV4 opt-in |
| SNS | One call to email; free tier | Silent until the subscription is confirmed |

### Q4. Onboarding (zero to hello world), per tool

- **Ring APIs:**
  - **Fast:** credentials appear once at app creation; with a 30-minute sandbox token, `GET /v1/devices` returned the Playground Device on the first try, within the first hour.
  - **Slowed by:** Getting Started saying a physical device is required (F1), and the private-app fields being easy to miss in the docs (F2).
- **Ring Appstore MCP:** added to the IDE by hand (the console's "Connect your IDE" panel doesn't list it). The first real question (nonce validation) returned the exact doc with code.
- **Ring Playground:**
  - **Instant:** the device was there and streaming immediately.
  - **Undocumented:** what the buttons do (switch clips, send no webhooks) had to be found by experiment.
- **Amazon Bedrock:** never reached hello world. The code was ready early, but every model call fails at the account level (support case open). Diagnosing it took several CLI checks because the error was the same everywhere.
- **Strands Agents:** `pip install`, and a scripted model driving two tools within minutes. The sticking points were the `self` naming rule and a Starlette/FastAPI version clash.
- **DynamoDB / S3 / SNS:** one setup script created the table, bucket and topic on the first run. The only snags:
  - **DynamoDB:** designing for consistency.
  - **SNS:** confirming the email subscription before anything is delivered.

### Q5. Would you build with these devices and services again?

**Yes.**
- **Ring:** the APIs themselves were clean and precisely documented. WHEP, webhook signing and account linking behaved exactly as specified, and the device list worked on the first call. What held us back was the sandbox (no webhooks, account linking blocked without a paid plan), not the API design. With those fixed, a second Ring app would be much faster.
- **AWS:**
  - **Strands:** made a well-tested agent with real guard rails possible even before Bedrock worked.
  - **DynamoDB, S3, SNS:** gave production-style storage and alerts with no always-on infrastructure.
  - **Bedrock:** I'd use it again once account access works.

*(Per-tool answers for question 5: PRODUCT_FEEDBACK.md.)*

### [Optional] Feature requests

See "Feature requests" below (5 requests with priority).

### [Optional] Friction log

See "Friction log" below (link plus a 12-entry summary).

### [Optional] Project testing link

Leave blank: there's no hosted deployment. Judges run it from the repo (README → "Run it").

### Are you submitting for the Open Source Mini Challenge?

**Yes**, as a new open-source project created during the hackathon window (MIT license). **[YOU]** This needs the repo to be **public** first.

- **Contribution URL:** https://github.com/arnavgupta2004/RingCare
- **Project repository URL:** https://github.com/arnavgupta2004/RingCare
- **GitHub username:** arnavgupta2004
- **Description:** see "Open Source Mini Challenge description" below.

#### Open Source Mini Challenge description

**What I did.** DoorSight is a new open-source project, MIT-licensed, built from scratch during the hackathon window (first commit 6 October 2026). It's a complete, working reference integration for the Ring Appstore APIs in Python, plus a doorstep assistant for older and low-vision residents built on top of it.

**How it works.**
- **Ring integration** (backend/ring/), with nothing guessed: every endpoint detail came from Ring's docs:
  - HMAC webhook verification over the raw body.
  - WHEP live-video capture with aiortc (video-only, retry on stalled streams, sessions always closed).
  - One-way account linking: sign-in on the Account Link URL, constant-time nonce matching, App-Integrations POST + PATCH, refresh tokens that rotate, and a log of which token each API call used.
- **The doorstep assistant:** a Strands agent with eight tools turns each Ring event into decisions: package arrived, reminder, possible missing package, unusual-hour visitor.
  - **Guard rails in code:** they live in the tools, e.g. camera-view fingerprints stop false "missing package" alarms from a different camera.
  - **Detection:** YOLO-World finds parcels locally.
  - **AWS:** DynamoDB, S3 and SNS store state, snapshots and caregiver email under a least-privilege IAM user.
  - **Web views:** React views for the resident (screen-reader-first, 0 axe violations) and the caregiver.
- **Reusable test tooling:** 262 tests, including a fake Ring OAuth/API server (tests/fake_ring.py) and a scripted Strands model (tests/fakes.py). Other Ring developers can test their linking flow and agent loop without a paid Ring plan or a live model.

**Why it matters.**
- **Ring has no official partner SDK,** and the sandbox can't send webhooks or complete account linking. DoorSight gives other developers working, tested code for those hard parts, plus a fake Ring server to test against.
- **A non-security use of Ring:** helping people stay independent at their own front door.
- **Documented friction:** a 12-entry friction log and five feature requests, to help Ring improve the developer experience.

### Are you submitting for the AWS Builder Mini Challenge?

**Yes.**

### AWS Builder: which AWS services did you incorporate and how?

Paste "AWS Builder: AWS services used and how" below.

### Province, if you reside in Canada

**[YOU]** "N/A" unless you live in Canada.

### Primary track(s)

**Ring**

### Code repository URL

https://github.com/arnavgupta2004/RingCare

**[YOU]** The repo is currently **private**. Either make it **public** (the MIT `LICENSE` then shows in the About section), or keep it private and share it with **testing@devpost.com** and **@AmazonAppDev**.

### New or existing prior to August 31, 2026?

**New.** The first commit is 6 October 2026 (the project skeleton), and everything was built during the submission period.

### Upload a file (optional, ≤ 35 MB)

Optional. A good choice is `video/demo_tts.mp4` (≈ 10 MB) as a backup copy of the demo video, or a zip of `docs/` (architecture, feedback, feature requests).

### Submitter type / Organization / Country

- **Submitter type:** **[YOU]** (likely "Individual")
- **Organization:** "N/A" unless you represent one
- **Country of residence:** **[YOU]**

---

## Project name

DoorSight

## Elevator pitch (≤ 200 characters)

A Ring doorstep assistant for older and low-vision residents: it watches live video, explains what's at the door in calm, large-text updates, and alerts a caregiver only when it matters.

*(186 characters)*

## Video

https://youtu.be/SCt6MBS1vvI

## Try it out

- Code: https://github.com/arnavgupta2004/RingCare
- Demo video: https://youtu.be/SCt6MBS1vvI

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
> The package scene is live: the Ring Playground, then a real WHEP capture and detection on fresh frames (the capture wait is shortened on screen). The other scenes replay earlier real Ring sandbox captures through the live DoorSight server. The agent runs on its rules brain while Bedrock access is pending.
