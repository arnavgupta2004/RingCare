# Product feedback — draft

Draft answers for the hackathon's product-feedback questions, one section per tool, based on what happened while building DoorSight. The detailed reproduction steps are in [FRICTION_LOG.md](../FRICTION_LOG.md); requests are in [FEATURE_REQUESTS.md](FEATURE_REQUESTS.md).

> **Before submitting:**
> 1. Replace the five question headings below with Devpost's exact wording; these are placeholders.
> 2. Fill in every **✍️ Your opinion** spot.
> 3. Cut whatever doesn't fit the form's length limits.

Placeholder questions used in every section:
1. **How did you use it?**
2. **What worked well?**
3. **What was difficult or confusing?**
4. **What would you change or add?**
5. **Overall, would you use it again / recommend it?**

---

## Ring APIs (Amazon Vision API)

**1. How did you use it?**
- **Device discovery:** `GET /v1/devices?include=status,capabilities` to find the doorbell.
- **WHEP live video:** a short session per event, with frames analysed locally (aiortc, video-only `recvonly`).
- **Webhooks:** HMAC-signed v1.1 webhooks as the event source.
- **One-way account linking:** Token Exchange URL, Account Link URL with nonce matching, App-Integrations POST + PATCH, refresh tokens. All of it runs at runtime in `backend/ring/`.

**2. What worked well?**
- **The WHEP flow:** POST an SDP offer, get `201` with a `Location`, `DELETE` to close. It is simple, standard and worked first time with a Python WebRTC stack.
- **Signing scheme:** one HMAC key for both webhooks (hex) and nonces (URL-safe Base64) is easy to implement, and the docs explain the encoding difference clearly.
- **Linking docs:** the nonce algorithm and the App-Integrations lifecycle (POST → `awaiting`, PATCH → `completed`) are precise enough to implement and test against a fake server.

**3. What was difficult or confusing?**
- **No package event (F5):** there's no documented package event type, and `subType` placement differs between pages.
- **Token Exchange contract (F6):** the response and retry behaviour aren't specified.
- **Undocumented fields (F7):** the device list returns fields the docs don't mention.
- **One-way disconnect (F11):** one-way apps can't revoke a user's access from the partner side; `DELETE` returns 403.

**4. What would you change or add?**
- A package/delivery event (or the full `subType` list).
- An OpenAPI spec kept in sync with the live API.
- A Token Exchange contract table.
- DELETE app-integrations for one-way apps.

**5. Overall:** ✍️ *Your opinion: would you build on it again, and for what?*

---

## Ring MCP (Ring Appstore docs server)

**1. How did you use it?**
- **Every endpoint detail came from it:** `search_docs` and `get_doc` covered nonce validation, the token exchange request, the webhook v1.1 payload and `X-Signature` header, WHEP sessions, App-Integrations, refresh tokens and private-app Connect. The project rule was "never guess an endpoint".

**2. What worked well?**
- **Accurate, citable answers:** specific questions returned the exact page with code examples. That was faster than browsing, and precise enough to catch subtle rules: use the HMAC key as raw UTF-8, the 60 s auth-code window, the mandatory PATCH after the link POST, and DELETE being unavailable to one-way apps.

**3. What was difficult or confusing?**
- **Broad questions fail:** they return `clarification_needed` with no results. A query about sandbox clip licensing came back empty.
- **Session drops:** the server disconnected once mid-session and had to reconnect.
- **IDE setup (F3):** the console's **Connect your IDE** panel doesn't list Claude Code, so it was added manually.

**4. What would you change or add?**
- Partial results or suggested pages instead of an empty "clarification needed".
- Coverage of sandbox/Playground behaviour (which actions send webhooks, stream length, clip credits).
- Setup steps for more MCP clients.

**5. Overall:** ✍️ *Your opinion*

---

## Ring Playground (developer sandbox)

**1. How did you use it?**
- **A target to build against:** the Playground Device (DoorbellPro) for device discovery and all live-video work, and the 30-minute sandbox OAuth token for development.
- **Scene switching:** the Package / Vehicle / Motion buttons switch which stock clip the device streams. The demo relies on that.

**2. What worked well?**
- **A real API without hardware:** a working device and a real WHEP stream, without buying a doorbell.
- **Realistic clips:** a parcel on a snowy front step, a parking lot with cars. Good material for computer vision.

**3. What was difficult or confusing?**
- **No webhooks (F9):** the buttons don't send webhooks, so the event flow can't be tested live.
- **Linking blocked (F12):** live account linking is blocked at Connect without a Ring Protect plan.
- **Short, unsignalled streams (F10):** streams sometimes end after 10–15 s with no signal, and the first session after a button click stalls.
- **Limited sandbox (F4):** one device, no sensors or chime, no event history.
- **Short tokens:** the sandbox token lasts 30 minutes, so it needs regenerating often during development.

**4. What would you change or add?**
- Real signed webhooks from Playground actions, or a "send test webhook" button.
- Let the Playground account complete account linking.
- A longer-lived sandbox token, or a refresh token.
- Simulated sensors and chime, and seeded event history.

**5. Overall:** ✍️ *Your opinion*

---

## Amazon Bedrock

**1. How did you use it?**
- **Two Claude Haiku 4.5 roles** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`):
  - **Vision model:** describes 3 representative frames per event as strict, validated JSON, including one calm sentence for the resident (`backend/vision/describe.py`, Converse API with image blocks).
  - **Agent brain:** chooses tools and writes messages (`backend/agent/brains.py`, Strands `BedrockModel`).
- **Automatic switch:** `scripts/check_bedrock.sh` decides at startup whether to use it.

**2. What worked well?**
- **One API for any model:** Converse lets you switch models with one environment variable.
- **Account-level diagnosis:** `get-foundation-model-availability` and a tiny Nova Lite call separate an account-level block from a model-level one.

**3. What was difficult or confusing?**
- **Every model refused:** this account gets `ValidationException: Operation not allowed` for every model (Anthropic, Amazon Nova and others) in every region tried. `authorizationStatus` is `NOT_AUTHORIZED` even for models normally available by default, and the error message doesn't say why or what to do (support case open). So Bedrock is code-complete but not live in this project.
- **Streaming needs a second permission:** `ConverseStream`, which Strands uses, needs `bedrock:InvokeModelWithResponseStream` as well as `bedrock:InvokeModel`. It's easy to miss in a least-privilege policy.
- **Login credentials need an extra package:** boto3 with `aws login` credentials requires `botocore[crt]`.

**4. What would you change or add?**
- An actionable error for account-level blocks: which check failed and where to fix it.
- A single "can this account invoke models?" status in the console.

**5. Overall:** ✍️ *Your opinion*

---

## Strands Agents

**1. How did you use it?**
- **One agent, eight tools:** start_live_capture, describe_scene, get_package_state, update_package_state, get_visit_baseline, notify_resident, notify_caregiver, write_daily_digest. The tools are methods bound to a per-event context.
- **Swappable brain:** `BedrockModel` in production; a scripted fake `Model` in tests that streams pre-written tool calls through the real agent loop.

**2. What worked well?**
- **`@tool` on methods, including async ones:** turns docstrings and type hints into clean tool specs.
- **Tool exceptions go back to the model** as error results instead of crashing, which suits server-side guard rails.
- **The `Model` interface is small enough to fake,** so the agent loop, tool wiring and error handling were all tested without Bedrock.

**3. What was difficult or confusing?**
- **The first parameter must be named `self`:** any other name (e.g. `s`) becomes a required tool argument, and the agent's calls fail silently.
- **Model errors are wrapped** in `EventLoopException`, so code checking for the original exception type has to look inside.
- **Dependency clash:** installing `strands-agents` upgraded Starlette, which broke the pinned FastAPI version until FastAPI was upgraded.

**4. What would you change or add?**
- A built-in test or mock model for scripted tool calls.
- A clearer error when a tool's spec includes `self`-like parameters.
- Looser dependency bounds.

**5. Overall:** ✍️ *Your opinion*

---

## Amazon DynamoDB

**1. How did you use it?**
- **One on-demand table** (`doorsight-state`) holds events, packages, notifications and linked-account tokens (`backend/store/dynamodb.py`).
- **Same interface as SQLite,** selected with `STATE_BACKEND`. The same test suite runs against both, with DynamoDB mocked by moto.

**2. What worked well?**
- **No capacity planning:** on-demand mode, and a tiny cost for this workload.
- **Easy testing:** moto made the DynamoDB tests fast and offline.

**3. What was difficult or confusing?**
- **GSIs are eventually consistent:** the first design used a GSI, but the state machine reads its own writes immediately, so it was redesigned around base-table queries with `ConsistentRead` and a pointer item for lookups by id.
- **Type handling:** numbers come back as `Decimal`, and `None` attributes are dropped. Together they caused a real bug (a digest's empty `event_id` couldn't be read back), which the tests caught.

**4. What would you change or add?**
- Optional strongly consistent reads on a GSI, even at extra cost.
- Native `None`/`float` handling in the boto3 resource layer.

**5. Overall:** ✍️ *Your opinion*

---

## Amazon S3

**1. How did you use it?**
- **Event snapshots:** three representative frames per event, in a private bucket with SSE-S3 and a 30-day lifecycle rule. The web app only gets 1-hour presigned URLs (`backend/snapshots.py`).
- **Least privilege:** the app's IAM user can only put and get objects under `frames/`.

**2. What worked well?**
- **Presigned URLs:** private storage with browser-viewable images, and no proxying.
- **Simple bucket hardening:** public-access block, default encryption and lifecycle are each one CLI call.

**3. What was difficult or confusing?**
- **SigV4 isn't the default for presigning:** clients have to opt in, or URLs use the old signature format.
- **Upload failures raise a different exception:** a failed `upload_file` raises boto3's `S3UploadFailedError`, not `ClientError`, so a normal error handler misses it. Tests caught this.
- **Noisy CLI output:** `put-bucket-lifecycle-configuration` prints JSON in scripts.

**4. What would you change or add?**
- Make `upload_file` errors subclass `ClientError`.
- SigV4 by default for presigned URLs.

**5. Overall:** ✍️ *Your opinion*

---

## Amazon SNS

**1. How did you use it?**
- **Caregiver email:** alerts (possible missing package, unusual-hour activity) and the daily digest are published to the `doorsight-caregiver-alerts` topic with email subscriptions (`backend/notify.py`).
- **Resilient delivery:** each notification records whether it was sent, logged or failed, and falls back to a log file if publishing fails.

**2. What worked well?**
- **Little code, quick setup:** one `publish` call for email delivery; topic creation is idempotent, and subscription confirmation took a minute.
- **Free tier:** the first 1,000 emails a month cost nothing.

**3. What was difficult or confusing?**
- **Silent until confirmed:** messages aren't delivered until the email subscription is confirmed, and nothing in the publish response says so.
- **Subject limit:** email subjects max out at 100 characters.

**4. What would you change or add?**
- Surface "no confirmed subscribers" in the `publish` response or metrics.

**5. Overall:** ✍️ *Your opinion*

---

## AWS services used — summary

| Service | Used for | Live in this project? |
|-|-|-|
| Amazon Bedrock | Vision description + agent brain (Claude Haiku 4.5) | Code complete; account blocked from invoking models (support case open) |
| Strands Agents | Agent loop, tools, fallback | Yes (rules brain live; Bedrock brain tested with a scripted model) |
| Amazon DynamoDB | State store | Yes |
| Amazon S3 | Snapshots, presigned URLs | Yes |
| Amazon SNS | Caregiver emails, daily digest | Yes |
| AWS IAM | Least-privilege user for the server | Yes |
