# DoorSight

**A Ring doorstep assistant for older and low-vision residents, and the family member who looks out for them.** Every Ring event (package, vehicle, motion) goes to an agent that opens the doorbell's live video, works out what is actually at the door, and remembers what happened before. A resident gets calm, large-text, screen-reader-friendly updates and a big "I picked up the package" button. A remote caregiver gets short, evidence-based alerts by email when something needs attention: a package that disappeared without being collected, or a car at the door at 3 AM. Every alert says *why*: the score, the camera-view check, the agent's tool calls. Every message says where its information came from, and the system never claims more certainty than its evidence supports.

> Built for the Amazon Developer Hackathon (Ring track + AWS Builder challenge). Demo video: _(link to add)_

| Resident view | Caregiver view |
|-|-|
| ![Resident view: large text, package waiting, big pickup button](docs/screenshots/resident.png) | ![Caregiver view: 3 AM alert with agent reasoning, daily digest](docs/screenshots/caregiver.png) |

## Judging criteria → features

The four criteria are weighted equally (Tech Implementation breaks ties). The friction log, which can add up to a 10% bonus, is [FRICTION_LOG.md](FRICTION_LOG.md): 12 entries ordered by severity, with feature requests in [docs/FEATURE_REQUESTS.md](docs/FEATURE_REQUESTS.md).

### 1. Tech Implementation
*How well it is built, and how effectively it uses the required APIs, SDKs and device capabilities.*
- **Ring device capabilities, called at runtime:**
  - **Live video:** WHEP sessions on the doorbell (aiortc, video-only `recvonly`, 1 frame/s, capture window starting at the first decoded frame, automatic retry, sessions always closed with `DELETE`). In `backend/vision/capture.py`.
  - **Device discovery:** `GET /v1/devices?include=capabilities` picks the video-capable device (`backend/events.py`).
  - **Webhooks:** HMAC-SHA256 verification over the raw body; returns 200 immediately and processes in the background (`backend/ring/signatures.py`, `backend/main.py`).
  - **Account linking (one-way):** sign-in on the Account Link URL, constant-time nonce matching, App-Integrations `POST` + mandatory `PATCH`, refresh tokens with rotation, and a log of which token each call used (`backend/ring/accounts.py`).
- **AWS SDKs:** Strands Agents (agent loop, 8 tools), Bedrock Converse (vision as strict JSON, Claude Haiku 4.5), DynamoDB, S3 and SNS via boto3. The server runs under a least-privilege IAM user (see AWS Builder below).
- **Built to be trusted:**
  - **Guard rails in the tools:** a capture from a different camera view can never mark a package missing (perceptual-hash view fingerprints, thresholds measured on real captures, `backend/vision/fingerprint.py`, DECISIONS.md D6). Alerts must match the package state.
  - **Fallbacks:** if the model fails or times out, the rules brain finishes the event without duplicate alerts.
- **Vision:** YOLO-World open-vocabulary detection ("cardboard box", "package", "parcel", "person", "car", "truck", "van"). It found the parcel in 19 of 20 sandbox frames, where a standard COCO model found nothing (DECISIONS.md D1).
- **Tested:** 261 pytest tests:
  - **Both stores:** the same store and doorstep tests run against SQLite and DynamoDB (moto).
  - **Agent:** the real Strands loop is driven by a scripted fake model (`tests/fakes.py`), and the rules brain is proven equivalent to the reference rules.
  - **Account linking:** tested end to end against a fake Ring OAuth/API server (`tests/fake_ring.py`).
- **Honest status:** Bedrock is code-complete, but this AWS account can't invoke models yet, so the rules brain and a labelled `stub` description run instead. The switch is automatic. See "What's real and what's simulated".

### 2. Design
*A complete, coherent product experience, with an interaction model that is intuitive for the target device or platform.*
- **One story from doorbell to people:** a Ring event leads to a capture, an understanding of the scene, the right person being told, and the resident or caregiver acting on it. Package: arrival message → gentle reminder after 3 h → one-tap "I picked it up" → or a possible-theft alert if it vanishes. Visits: unusual-hour alerts and a daily digest.
- **`/resident`, designed for older, low-vision users** (`web/src/pages/Resident.tsx`):
  - **Look:** white on black, 24 px base text, one large action (a 120 px "I picked up the package" button, shown only when a package is waiting), and the photo of that package.
  - **Screen readers:** updates are announced once through a polite live region, and focus moves sensibly after the button disappears.
  - **Checked:** 0 axe-core violations (WCAG 2.2 A/AA) at desktop and phone width.
- **`/caregiver`, built around evidence** (`web/src/pages/Caregiver.tsx`):
  - **Alerts:** each shows a snapshot, a plain explanation, the unusual-hour score and the agent's reasoning with every tool call.
  - **Source labels:** every item is labelled `bedrock`, `stub` or `rules`.
  - **Digest:** the daily digest is shown here and also emailed.
- **The Ring side of the experience:** a sign-in page on the Account Link URL, and a status page on the App Homepage URL (account ID, token expiry, Disconnect).
- **Voice:** messages to the resident are calm and short ("Gentle reminder: the package left at your door at 2:10 PM is still there."). Messages to the caregiver quote their evidence.

### 3. Potential Impact
*A credible, specific case for solving customer needs, and an audience beyond the hackathon.*
- **The customer need:**
  - **The resident:** someone living alone with low vision or limited mobility can't easily see what's at the door, whether a delivery arrived, or whether it's still there.
  - **The caregiver:** a family member who lives elsewhere wants to know when something is wrong, not get every motion alert.
- **What DoorSight does about it:**
  - **Turns events into plain answers:** "a package is at your door".
  - **Reminds before parcels are forgotten:** a gentle reminder after 3 hours (`backend/doorstep.py`).
  - **Alerts the caregiver only when it matters:** a possible missing package confirmed from the arrival view, or activity at an hour that is unusual *for this home* (`config/visit_profile.json` plus observed visits).
  - **Sends a daily digest** instead of a stream of notifications.
- **Beyond the hackathon:**
  - **Any Ring doorbell:** it uses standard Ring APIs and needs no extra hardware.
  - **Real-account linking:** implemented; it awaits a Ring Protect account to verify live.
  - **Ready for production use:**
    - **Data:** on-demand DynamoDB, private S3 with 30-day expiry, and a least-privilege IAM user.
    - **Cost:** no always-on infrastructure.

### 4. Quality of the Idea
*A creative use of the required tools, and a genuine understanding of the developer ecosystem and end-user needs.*
- **Ring's creative examples it covers:**
  - **Accessibility aid:** a screen-reader-first, large-text resident view.
  - **Caretaking:** remote caregiver alerts and a daily digest.
  - **Package/delivery management:** arrival, reminder, pickup, possible-missing.
  - **Event-based triggers:** every Ring event starts the agent.
  - **Multi-camera (partly):** the package logic tracks which camera view a package arrived in, so captures from another camera can't trigger false alarms.
- **Non-security by design:** the goal is independence and reassurance, not surveillance.
  - **Resident wording:** the resident never sees scores or alarm words.
  - **Caregiver wording:** caregiver alerts say "possible missing package", not "stolen". That is fixed in the rules templates; for a Bedrock brain it is set by the agent's instructions.
- **Ecosystem understanding:**
  - **Docs:** every endpoint detail came from the Ring Appstore MCP docs.
  - **Friction log:** sandbox limits are documented with reproduction steps (12 entries).
  - **Workarounds without hiding gaps:** `/simulate-event` reuses the real webhook path, and linking is tested against a fake Ring server.

### AWS Builder challenge

| Service | How DoorSight uses it | Code |
|-|-|-|
| Amazon Bedrock | Vision description (strict JSON) and the agent brain, Claude Haiku 4.5, via Converse / Strands `BedrockModel` | `backend/vision/describe.py`, `backend/agent/brains.py` |
| Strands Agents | Agent loop with 8 tools, fallback and timeout handling | `backend/agent/` |
| Amazon DynamoDB | Events, packages, notifications and linked-account tokens. On-demand, strongly consistent single-table design | `backend/store/dynamodb.py` |
| Amazon S3 | Event snapshots: private bucket, SSE-S3, 30-day expiry, 1-hour presigned URLs for the UI | `backend/snapshots.py` |
| Amazon SNS | Caregiver alert emails and the daily digest | `backend/notify.py` |
| AWS IAM | The server runs as `doorsight-app`, a least-privilege user (policy below) | `docs/iam/doorsight-app-policy.json` |

The server never uses root credentials. Its IAM user can only touch the DoorSight table, the snapshot objects, the caregiver topic and the two Bedrock models it calls. Listing buckets, tables or topics, deleting objects, and anything in IAM are all denied (verified):

```json
{ "Sid": "StateTable",       "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Query","dynamodb:BatchWriteItem"], "Resource": "…:table/doorsight-state" },
{ "Sid": "SnapshotObjects",  "Action": ["s3:PutObject","s3:GetObject"], "Resource": "arn:aws:s3:::doorsight-snapshots-<account>/frames/*" },
{ "Sid": "CaregiverAlerts",  "Action": "sns:Publish", "Resource": "…:doorsight-caregiver-alerts" },
{ "Sid": "BedrockModels",    "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"], "Resource": ["Claude Haiku 4.5 + Nova Lite inference profiles and models"] }
```

## What's real and what's simulated

| Part | Status |
|-|-|
| Ring device discovery (`GET /v1/devices`) | **Real**, against the Ring sandbox ("Playground Device") |
| Live video capture (WHEP) | **Real**: frames are captured from the sandbox stream. The sandbox streams stock clips; the Playground's Package / Vehicle / Motion buttons switch the clip |
| Ring webhooks | **Simulated.** The Playground buttons don't send webhooks (friction F9), so `POST /simulate-event` builds the same v1.1 payload and runs it through the same handler. Signature verification is implemented and tested |
| Account linking (one-way) | **Implemented, verified against a fake Ring server** (53 tests). Live linking needs a Ring Protect plan or trial (friction F12); until then the app uses the sandbox token |
| Object detection (YOLO-World) | **Real**, runs locally on the captured frames |
| Bedrock vision + agent brain | **Code complete, not yet live.** This AWS account can't invoke any Bedrock model yet (support case open). The rules brain and a clearly labelled `stub` description are used instead; the switch is automatic once `scripts/check_bedrock.sh` passes |
| DynamoDB, S3, SNS | **Real**, verified live under the least-privilege IAM user |
| Time | **Simulated for demos:** a demo clock (`DEMO_TIME_OFFSET`, `POST /demo/clock`) lets the story jump to 3 AM or skip 3 hours. Every event stores both real and simulated time |

## Architecture

```mermaid
flowchart TB
  subgraph Ring["Ring · Amazon Vision API"]
    direction LR
    WH["Webhooks<br/>HMAC-signed v1.1"]
    OAUTH["OAuth + App-Integrations<br/>account linking"]
    DEV["GET /v1/devices"]
    WHEP["WHEP live video"]
  end
  subgraph Backend["DoorSight backend · FastAPI"]
    direction TB
    EP["Ring console endpoints<br/>/webhook · /token · /link · /home<br/>+ /simulate-event (dev trigger)"]
    ACC["Accounts & tokens<br/>linked token or sandbox · auto refresh"]
    AG["Strands agent · 8 tools<br/>rules brain or Bedrock brain"]
    CAP["Frame capture<br/>aiortc · 1 frame/s · ≤ 20 s"]
    VIS["Scene analysis<br/>YOLO-World + vision model"]
    DS["Doorstep rules & guard rails<br/>package lifecycle · camera-view check<br/>unusual-hour score · daily digest"]
    API["UI API · /state · pickup · demo clock · digest"]
  end
  subgraph AWS["AWS · least-privilege IAM user"]
    direction LR
    BR["Amazon Bedrock<br/>Claude Haiku 4.5"]
    DDB["Amazon DynamoDB"]
    S3["Amazon S3"]
    SNS["Amazon SNS"]
  end
  subgraph Web["Web app · React + Vite"]
    direction LR
    RES["/resident"]
    CG["/caregiver"]
  end
  WH --> EP
  OAUTH <--> EP
  EP --> AG
  AG --> CAP
  WHEP --> CAP
  DEV --> ACC
  ACC -. token .-> CAP
  CAP --> VIS
  VIS --> AG
  AG --> DS
  VIS -. vision .-> BR
  AG -. agent brain .-> BR
  DS --> DDB
  DS --> S3
  DS --> SNS
  DS --> API
  API --> RES
  API --> CG
```

The rendered version, with file paths, is [docs/architecture.png](docs/architecture.png); its source is [docs/architecture.mmd](docs/architecture.mmd). Design decisions and measured thresholds are in [DECISIONS.md](DECISIONS.md).

**Event flow:**
1. A webhook (or `/simulate-event`) reaches the agent.
2. The agent runs `start_live_capture` → `describe_scene` → `get_package_state` → `update_package_state` → `get_visit_baseline` → `notify_resident` / `notify_caregiver`. Every step is recorded on the event.
3. Resident messages appear in `/resident`; caregiver alerts are also emailed through SNS.

## Run it

**Prerequisites:**
- Python 3.12, Node 20+ and Chrome (only for the accessibility check).
- A Ring developer account with an app (Client ID, Client Secret, HMAC key).
- Optionally: an ngrok static domain and an AWS account.

**1. Install and configure**
```bash
git clone https://github.com/arnavgupta2004/RingCare.git && cd RingCare
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
In `.env`, fill in `RING_CLIENT_ID`, `RING_CLIENT_SECRET` and `RING_HMAC_KEY`. For `RING_ACCESS_TOKEN`, generate a sandbox OAuth token in the Ring developer console; it lasts 30 minutes and is re-read on every call, so you can refresh it without restarting.

**2. Check Ring access and run the tests**
```bash
python scripts/list_devices.py --include      # should list "Playground Device"
pytest                                        # 261 tests, no network or AWS needed
```

**3. Run the backend** (SQLite and local files by default; no AWS needed)
```bash
uvicorn backend.main:app --port 8000
```

**4. Run the web app** at http://localhost:5180/resident and http://localhost:5180/caregiver:
```bash
cd web && npm install && npm run dev
```

**5. Play the demo story** without waiting for real time. It replays real sandbox captures through the agent and finishes with the daily digest:
```bash
python scripts/demo_story.py --db data/doorsight.db                   # full story, shown in the web app
python scripts/demo_story.py --db data/doorsight.db --until reminder  # stop with a package waiting
```

**6. Trigger a live capture.** First click the matching event (Package / Vehicle / Motion) in the Ring Playground, then either use the caregiver view's "Simulate" buttons or:
```bash
curl -X POST localhost:8000/simulate-event -H 'Content-Type: application/json' -d '{"event_type":"package","wait":true}'
```

**7. Ring console endpoints** (optional; the console requires HTTPS):
```bash
ngrok http 8000 --url https://<your-static-domain>
```
Set these in the console's Account linking form:
- **Account Link URL:** `https://<domain>/link`
- **App Homepage URL:** `https://<domain>/home`
- **Token Exchange URL:** `https://<domain>/token`
- **Webhook URL:** `https://<domain>/webhook`

The sign-in on `/link` uses one demo user; set its password with `python scripts/set_demo_password.py`.

**8. AWS backends** (optional):
```bash
./scripts/aws_setup.sh --email you@example.com --enable   # table, private bucket, topic; switches .env to DynamoDB + S3
./scripts/aws_iam_setup.sh                                # least-privilege user doorsight-app, AWS_PROFILE=doorsight
./scripts/check_bedrock.sh                                # can this account call Bedrock?
python scripts/demo_story.py --aws                        # the story on DynamoDB + S3 + SNS
./scripts/aws_teardown.sh --dry-run                       # preview deleting all of it
```
`aws_setup.sh` and `aws_iam_setup.sh` need admin credentials; the server itself only ever uses the restricted profile. Caregiver emails start once the SNS subscription email is confirmed.

## Endpoints

| Route | Ring console field | Purpose |
|-|-|-|
| `GET/POST /link` | Account Link URL | Checks `time` (10 min window), requires DoorSight sign-in, matches the `nonce` (HMAC-SHA256, URL-safe Base64) to an unclaimed token, confirms with Ring (POST + PATCH app-integrations) |
| `POST /token` | Token Exchange URL | Exchanges the form-encoded auth code, reads the account ID from `GET /v1/users/me`, stores the tokens |
| `GET /home` | App Homepage URL | Linked status; after sign-in: Ring account ID, token expiry, token source, Disconnect |
| `POST /webhook` | Webhook URL | Verifies `X-Signature: sha256=<hex>`, returns 200 immediately, handles the event in the background |
| `POST /simulate-event` | — | Dev trigger: same payload shape and handler as a webhook |
| `POST /digest` | — | Agent writes and sends the caregiver's daily digest |
| `POST /demo/clock` | — | Set or advance simulated time, then run reminders |
| `POST /packages/{id}/picked-up` | — | The resident's "I picked up the package" button |
| `GET /state` | — | Everything the web app shows |
| `GET /health` | — | Liveness |

## Repository layout

```
backend/
  main.py             FastAPI app and routes
  events.py           webhook normalisation, live capture, hand-off to the agent
  agent/              Strands agent: tools, rules / Bedrock brains, runner, prompts
  doorstep.py         package lifecycle, guard rails, unusual-hour score, digest
  ring/               Ring client, HMAC signatures, accounts and token refresh
  vision/             WHEP capture, YOLO-World, Bedrock vision, camera-view fingerprints
  store/              StateStore interface: SQLite and DynamoDB implementations
  snapshots.py        local or S3 snapshots
  notify.py           SNS or log delivery
  auth.py             demo-user sign-in for account linking
web/                  React + Vite resident and caregiver views
scripts/              demo story, AWS setup/IAM/teardown, Bedrock check, tools
tests/                261 tests, fake Ring server, scripted fake model
docs/                 architecture, IAM policy, screenshots, product feedback, feature requests
DECISIONS.md          design decisions with measurements
FRICTION_LOG.md       developer-experience friction, ordered by severity
```

## Credits

The Ring sandbox live-view clips are stock footage licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Frames from them appear in the screenshots and the demo video.

| Clip (as streamed by the sandbox) | Title, author, source | License |
|-|-|-|
| Snowy bird feeders (default view) | _(to fill in)_ | CC BY 4.0 |
| Front step with a parcel ("Package") | _(to fill in)_ | CC BY 4.0 |
| Parking lot and driveway ("Vehicle") | _(to fill in)_ | CC BY 4.0 |

## License

MIT — see [LICENSE](LICENSE).
