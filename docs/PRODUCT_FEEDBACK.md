# Product feedback — draft

Draft answers to the hackathon's five product-feedback questions, one section per tool, based on what happened while building DoorSight. Reproduction steps are in [FRICTION_LOG.md](../FRICTION_LOG.md) (IDs F1–F12); requests are in [FEATURE_REQUESTS.md](FEATURE_REQUESTS.md).

> **Before submitting:**
> 1. Review the question-5 answers marked *(draft — Arnav to confirm)* and change any that don't match your view.
> 2. Trim to the form's length limits.

The five questions, asked per tool:
1. Which developer tools, APIs and SDKs did you use and for what?
2. What worked well? (setup, documentation, testing, performance, reliability)
3. What needs work? (errors, docs sections, missing features, compatibility issues, limitations, workarounds)
4. How was your onboarding experience (zero to hello world)?
5. Would you build with these devices and services again? Yes/No and why.

---

## AWS Builder — AWS services used and how

*(Required for the AWS Builder answer; paste it into the AWS-related feedback answer.)*

| Service | How DoorSight uses it | Live? |
|-|-|-|
| **Amazon Bedrock** | Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) in two roles: (1) vision, describing 3 representative doorbell frames per event as strict, validated JSON, including one calm sentence for the resident (Converse API with image blocks, `backend/vision/describe.py`); (2) the agent brain that decides which tools to call and writes the messages (`backend/agent/brains.py`) | Code complete; this account can't invoke any model yet (support case open). A labelled `stub` description and the rules brain run instead, and the switch happens automatically when `scripts/check_bedrock.sh` passes |
| **Strands Agents** | The agent loop: 8 tools (capture, describe, package state/update, visit baseline, notify resident/caregiver, daily digest), with timeout and fallback to a deterministic rules brain. Every tool call and the agent's reason are stored on the event (`backend/agent/`) | Yes (rules brain live; Bedrock brain tested with a scripted model) |
| **Amazon DynamoDB** | The state store: events, packages, notifications and linked Ring-account tokens. One on-demand table with strongly consistent reads; selectable with `STATE_BACKEND` (`backend/store/dynamodb.py`) | Yes |
| **Amazon S3** | Event snapshots: 3 frames per event in a private bucket (public access blocked, SSE-S3, 30-day lifecycle). The web app only gets 1-hour presigned URLs (`backend/snapshots.py`) | Yes |
| **Amazon SNS** | Caregiver alert emails (possible missing package, unusual-hour activity) and the daily digest. Each notification records sent / logged / failed, with a log-file fallback (`backend/notify.py`) | Yes |
| **AWS IAM** | The server runs as `doorsight-app`, a least-privilege user that can only reach the DoorSight table, the snapshot objects, the caregiver topic and the two Bedrock models (`docs/iam/doorsight-app-policy.json`) | Yes |

Setup and teardown are scripted and idempotent: `scripts/aws_setup.sh`, `scripts/aws_iam_setup.sh` and `scripts/aws_teardown.sh`.

---

## Ring APIs (Amazon Vision API)

**1. What did you use and for what?**
- **Device discovery:** `GET /v1/devices?include=status,capabilities` finds the video-capable doorbell.
- **WHEP live video:** `POST /v1/devices/{id}/media/streaming/whep/sessions` plus `DELETE` captures about 20 s of frames per event, analysed locally (aiortc, video-only `recvonly`).
- **Webhooks:** HMAC-signed v1.1 webhooks are the event source; we verify the `X-Signature` header.
- **One-way account linking:**
  - **Token Exchange URL:** the auth code is exchanged at `oauth.ring.com`.
  - **Account Link URL:** HMAC nonce matching after the user signs in to DoorSight.
  - **Confirmation:** `POST` + `PATCH /v1/accounts/me/app-integrations`.
  - **Tokens:** `GET /v1/users/me` for the account ID; refresh tokens with rotation.

**2. What worked well?**
- **Documentation:** the nonce algorithm and the App-Integrations lifecycle (POST → `awaiting`, PATCH → `completed`) are precise enough to implement without guessing and to test against a fake server. The webhook signing page explains the hex-vs-Base64 difference clearly.
- **Setup:** WHEP is standard: SDP offer, `201` with a `Location`, `DELETE`. It worked first time with a Python WebRTC stack.
- **Performance:** the device list answered in about 1.7 s; frames arrived at about 30 fps once the stream started.
- **Testing:** the documented payloads made realistic fakes possible; the linking flow is covered by 53 tests against them.

**3. What needs work?**
- **Missing feature / docs (F5):** no package or delivery event is documented. The full `subType` list isn't published, and `subType` appears at `data.subType` on one page but is missing from the API reference's v1.1 example.
- **Docs (F6):** the Token Exchange URL has no contract (fields, expected response, retries). Workaround: return `200 {"status":"ok"}`, `400` or `502`.
- **Docs (F7):** `GET /v1/devices` returns fields the docs don't list (`image_url`, several null capability keys, `reported_at`). Workaround: parse defensively.
- **Limitation (F11):** one-way apps can't revoke a user from the partner side; `DELETE app-integrations` returns 403. Workaround: pause with `PATCH awaiting`, delete our tokens, and handle the `app_integration_removed` webhook.
- **Reliability (F10):** sandbox WHEP streams sometimes stop after 10–15 s with no end-of-stream signal. The first decodable frame takes 2.4–5.1 s.

**4. Onboarding (zero to hello world)**
- **Credentials:** creating the app showed the Client ID, Client Secret and HMAC key once (with a CSV download).
- **First call:** with a 30-minute sandbox OAuth token from the console, `GET /v1/devices` returned the Playground Device on the first try, within the first hour.
- **What slowed it down:**
  - **F1:** Getting Started says a physical device is required, though the sandbox is enough.
  - **F2:** the private-app account-linking fields are documented only in a short table at the end of the Configure section.
  - **F8:** the HMAC key looks Base64-encoded but must be used as raw text.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** The APIs themselves were clean and precisely documented. WHEP, webhook signing and account linking all worked as specified, and the device list worked on the first call. What held us back was the sandbox (no webhooks, account linking blocked without a paid plan), not the API design. With those fixed, building a second Ring app would be much faster.

---

## Ring Appstore MCP (docs server)

**1. What did you use and for what?**
- **`search_docs` and `get_doc` from the IDE for every endpoint detail:** nonce validation, the token exchange request, webhook v1.1 payload and signature header, WHEP sessions and codecs, App-Integrations POST/PATCH/DELETE, refresh tokens, and private-app Connect. The project rule was "never guess an endpoint".

**2. What worked well?**
- **Documentation access:** specific questions returned the exact page with code examples, much faster than browsing.
- **Precision:** it surfaced subtle rules that would have been easy to get wrong: use the HMAC key as raw UTF-8, exchange the auth code within 60 s, the mandatory PATCH after the link POST, and DELETE being unavailable to one-way apps.
- **Reliability:** answers matched the live API wherever we could check.

**3. What needs work?**
- **Errors:** broad questions return `clarification_needed` with no results instead of the closest pages. A query about sandbox clip licensing came back empty.
- **Reliability:** the server disconnected once mid-session and had to reconnect.
- **Coverage:** there's nothing on Playground behaviour (which actions send webhooks, stream length, clip credits).
- **Setup (F3):** the console's "Connect your IDE" panel lists Claude Desktop but not Claude Code.

**4. Onboarding (zero to hello world)**
- **Setup:** added to the IDE by hand from the generic MCP configuration.
- **First answer:** the first real question (nonce validation) returned the exact App Deployment section with Python code. Useful from the first query.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** It was the fastest way to get exact, citable answers about the Ring APIs, and it kept us from guessing endpoints. Better handling of broad questions, and coverage of Playground behaviour, would make it better still.

---

## Ring Playground (developer sandbox)

**1. What did you use and for what?**
- **Building target:** the Playground Device (DoorbellPro) for device discovery and all live-video work, and the 30-minute sandbox OAuth token for development.
- **Scene switching:** the Package / Vehicle / Motion buttons switch which stock clip the device streams. The demo relies on that.

**2. What worked well?**
- **Setup:** a real device and a real WHEP stream with no hardware purchase.
- **Testing:** the clips are realistic (a parcel on a snowy front step, a parking lot with cars), which is good material for computer vision. Switching clips with one click makes repeatable demos possible.

**3. What needs work?**
- **Missing feature (F9):** Playground actions don't send webhooks to the Webhook URL, so the event flow can't be tested live. Workaround: `POST /simulate-event` builds the same v1.1 payload and runs the same handler.
- **Limitation (F12):** live account linking stops at Connect without a Ring Protect plan or trial. Ring never calls the Token Exchange URL. Workaround: verified against a fake Ring server.
- **Reliability (F10):** streams can end after 10–15 s with no signal, and the first session after a button click usually stalls. Workaround: timeouts plus one retry.
- **Limitation (F4):** one device, no sensors or chime, empty event history.
- **Setup:** the sandbox token lasts 30 minutes and has no refresh token.

**4. Onboarding (zero to hello world)**
- **Instant:** the device was there and streaming immediately.
- **Gaps:** what the buttons do and don't do (switch clips, no webhooks) had to be discovered by experiment; it isn't documented.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes, with caveats.** A real device and live video stream without buying hardware is a big help, and the scene-switching clips made a repeatable demo possible. But without webhooks or account linking we had to simulate the two flows a real integration depends on.

---

## Amazon Bedrock

**1. What did you use and for what?**
- **Claude Haiku 4.5 through the Converse API** in two roles:
  - **Vision:** describes three representative doorbell frames per event as strict JSON (`scene_summary`, `package_visible`, `vehicle_visible`, `people_visible`, `confidence`, `accessible_description`). Every field is type-checked and an invalid reply gets one re-ask (`backend/vision/describe.py`).
  - **Agent brain:** reached through Strands `BedrockModel` (`backend/agent/brains.py`).

**2. What worked well?**
- **Setup:** Converse is one API for every model, so switching models is a single environment variable.
- **Diagnostics:** `get-foundation-model-availability` and a tiny Nova Lite call cleanly separated an account-level block from a model-level one. We turned that into `scripts/check_bedrock.sh`, which the server runs at startup to choose its brain.

**3. What needs work?**
- **Errors and limitations:** this account gets `ValidationException: Operation not allowed` for every model (Anthropic, Amazon Nova and others) in every region tried. `authorizationStatus` is `NOT_AUTHORIZED` even for models that are normally available by default. The error doesn't say which check failed or how to fix it (support case open). Workaround: the rules brain plus a detector-only description labelled `stub`.
- **Docs and compatibility:** `ConverseStream`, which Strands uses, needs `bedrock:InvokeModelWithResponseStream` as well as `bedrock:InvokeModel`. It's easy to miss when writing a least-privilege policy.
- **Compatibility:** boto3 with `aws login` credentials needs the extra `botocore[crt]` package.

**4. Onboarding (zero to hello world)**
- **Not reached:** the code was written and tested (with a fake client) early in the build, but the first real invocation has never succeeded on this account. Diagnosing the cause (account-wide, all regions, all models) took several CLI checks because the error message was the same everywhere.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes**, once access works. Converse and Strands made Bedrock straightforward to design for, and the same code runs unchanged when access is granted. But the account-level block with an unhelpful error cost real time, and we never got to see Haiku's live reasoning in this project.

---

## Strands Agents

**1. What did you use and for what?**
- **One agent, 8 tools:** the tools are methods bound to a per-event context.
- **Two brains:** `BedrockModel` for production, and a scripted fake `Model` in tests that streams pre-written tool calls through the real agent loop.
- **Recorded reasoning:** every tool call (input, output or error) and the agent's final reason are stored on the event and shown to the caregiver.

**2. What worked well?**
- **Setup:** `@tool` on instance methods, including async ones, turns docstrings and type hints into clean tool specs.
- **Reliability:** tool exceptions come back to the model as error results instead of crashing the run, which suits server-side guard rails.
- **Testing:** the `Model` interface is small enough to fake, so the agent loop, tool wiring, refusals, model errors and timeouts are all tested without Bedrock.

**3. What needs work?**
- **Errors:** the first parameter of a tool method must be named exactly `self`. Any other name becomes a required tool argument and the agent's calls quietly fail.
- **Errors:** model errors are wrapped in `EventLoopException`, so code checking the original exception type has to look inside.
- **Compatibility:** installing `strands-agents` upgraded Starlette, which broke the pinned FastAPI version until FastAPI was upgraded too.
- **Missing feature:** a built-in mock or scripted model for tests.

**4. Onboarding (zero to hello world)**
- **Fast:** after `pip install strands-agents`, a scripted model driving two tools ran within minutes.
- **The sticking points:** the `self` naming rule and the dependency clash.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** Method tools, error results returned to the model, and a small `Model` interface made a well-tested agent with real guard rails possible, even without a working model provider. The rough edges (the `self` naming rule, the dependency clash) were quick to work around.

---

## Amazon DynamoDB

**1. What did you use and for what?**
- **One on-demand table** (`doorsight-state`) holds events, packages, notifications and linked-account tokens.
- **Same interface as SQLite,** selected with `STATE_BACKEND`. The same test suite runs against both, with DynamoDB mocked by moto.

**2. What worked well?**
- **Setup:** on-demand mode means no capacity planning, and the table was created in one script run.
- **Testing:** moto made DynamoDB tests fast and offline.
- **Reliability:** strongly consistent base-table reads gave the state machine read-your-writes behaviour.

**3. What needs work?**
- **Limitation:** GSIs are only eventually consistent. Our first design used one, but the state machine reads its own writes immediately, so it was redesigned around base-table queries with `ConsistentRead` plus a pointer item for lookups by id.
- **Compatibility:** numbers come back as `Decimal`, and `None` attributes are dropped. Together they caused a real bug (a digest's empty `event_id` couldn't be read back), which the tests caught.

**4. Onboarding (zero to hello world)**
- **Smooth:** `create-table` plus `put_item`/`get_item` worked first time. The consistency question was the only design snag.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** On-demand capacity and moto-based tests made it painless for a small, read-your-writes workload. The consistency model just needs designing for up front.

---

## Amazon S3

**1. What did you use and for what?**
- **Event snapshots:** three representative frames per event, in a private bucket with SSE-S3 and a 30-day lifecycle. The web app only gets 1-hour presigned URLs.
- **Least privilege:** the app's IAM user can only put and get objects under `frames/`.

**2. What worked well?**
- **Setup:** public-access block, default encryption and lifecycle are one CLI call each.
- **Performance:** presigned URLs give the browser private images directly, with no proxying through the backend.

**3. What needs work?**
- **Errors:** a failed `upload_file` raises boto3's `S3UploadFailedError`, not `ClientError`, so a normal error handler misses it. Tests caught this.
- **Compatibility:** presigned URLs need SigV4 opted in on the client, or they come out in the old signature format.
- **Docs and setup:** `put-bucket-lifecycle-configuration` prints JSON in scripts.

**4. Onboarding (zero to hello world)**
- **Quick:** bucket creation, upload and presigned download worked in the first session.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** A private bucket with presigned URLs is the simplest secure way to show camera snapshots in a web app.

---

## Amazon SNS

**1. What did you use and for what?**
- **Caregiver email:** alerts (possible missing package, unusual-hour activity) and the daily digest are published to the `doorsight-caregiver-alerts` topic, which has an email subscription.
- **Resilient delivery:** each notification records whether it was sent, logged or failed, and falls back to a log file if publishing fails.

**2. What worked well?**
- **Setup:** topic creation is idempotent, the email subscription was confirmed in a minute, and delivery takes one `publish` call.
- **Cost and reliability:** free for the first 1,000 emails a month. Every publish call in testing succeeded and returned a message id.

**3. What needs work?**
- **Errors:** until the email subscription is confirmed, messages are silently not delivered; nothing in the `publish` response says so.
- **Limitation:** email subjects are limited to 100 characters.

**4. Onboarding (zero to hello world)**
- **Quick:** topic, subscription and first delivered email took a few minutes, once we knew to confirm the subscription.

**5. Would you build with it again?** *(draft — Arnav to confirm)* **Yes.** It's the quickest way to add reliable caregiver email; a clearer signal for unconfirmed subscriptions would make it even easier.
