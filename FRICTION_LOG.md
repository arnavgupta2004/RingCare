# Friction Log — Ring AppStore / Amazon Vision API

Developer-experience friction found while building DoorSight (October 2026), ordered by severity. IDs (F1–F12) are stable, so references elsewhere (README, DECISIONS.md) stay valid when entries move.

Each entry: **task · steps · expected vs actual · severity · workaround · suggestion**.

Severity:
- **High:** blocked a core part of the integration from being tested live.
- **Medium:** cost real time, or risked a wrong implementation.
- **Low:** confusing or cosmetic.

| ID | Severity | Summary |
|-|-|-|
| F9 | High | Playground simulator buttons don't send webhooks |
| F12 | High | Live account linking blocked at the Connect step (Ring Protect plan required) |
| F5 | Medium | No documented "package" event or full `subType` list, though the simulator has a Package button |
| F10 | Medium | Sandbox WHEP stream sometimes ends after ~10–15 s with no signal |
| F11 | Medium | One-way apps can't disconnect a user from the partner side |
| F4 | Medium | Sandbox has one doorbell, no sensors or chime, empty event history |
| F1 | Medium | Getting Started requires a physical device; the console offers a sandbox |
| F6 | Low | Token Exchange URL request/response contract is unspecified |
| F7 | Low | `GET /v1/devices` returns undocumented fields |
| F8 | Low | HMAC key looks Base64-encoded but must be used as raw text |
| F2 | Low | Private-app account-linking fields are easy to miss in the Configure docs |
| F3 | Low | "Connect your IDE" lists Claude Desktop but not Claude Code |

---

## High

### F9 · Playground simulator buttons don't send webhooks

| Field | Detail |
|-|-|
| Task | Confirm that simulated sandbox events reach the registered Webhook URL, so the event pipeline can be tested end to end |
| Steps | 1. Registered `https://<ngrok-static-domain>/webhook` as the Webhook URL in the private app's Account linking form. 2. Confirmed the server was reachable through ngrok (`/health` returned 200). 3. Clicked **Package**, then **Motion**, in the console Playground. 4. Watched the ngrok request inspector and `logs/webhooks.jsonl` |
| Expected vs actual | **Expected:** one HMAC-signed v1.1 webhook (e.g. `motion_detected`) per click. **Actual:** no request reached the Webhook URL; the ngrok inspector showed only our own test requests. Nothing in the docs says whether simulator actions produce webhooks at all |
| Severity | **High.** The webhook-driven event flow, the core of a Ring integration, can't be exercised live in the sandbox |
| Workaround | Added `POST /simulate-event {event_type}`, which builds the same v1.1 payload a webhook would carry and runs it through the same handler. Signature verification is covered by unit tests with locally signed payloads |
| Suggestion | Make Playground actions deliver real signed webhooks to the configured Webhook URL (or add a **Send test webhook** button that shows the response status), and document which actions produce which event type and payload |

### F12 · Live account linking blocked at the Connect step (Ring Protect plan required)

| Field | Detail |
|-|-|
| Task | Test the real one-way account linking flow (Token Exchange URL → Account Link URL → App-Integrations POST/PATCH) on the private app |
| Steps | 1. Set Account Link, App Homepage, Token Exchange and Webhook URLs (public HTTPS via ngrok). 2. App overview → **Connect** → **Log in with Ring**. 3. Signed in with a Ring account |
| Expected vs actual | **Expected:** Ring POSTs an authorization code to the Token Exchange URL, then redirects the browser to the Account Link URL. **Actual:** the flow stopped inside the Connect step; the server log shows no request from Ring on `/token` or `/link`. Ring's on-screen message: _(exact wording to be added)_. The FAQ states connected accounts need a Ring Protect plan or an active trial |
| Severity | **High.** Account linking, which certification depends on, can't be verified live without a paid plan or trial on the test account |
| Workaround | Kept the full implementation and verified it against a fake Ring server that issues and rotates tokens, serves `/v1/users/me`, generates nonces and checks App-Integrations POST/PATCH (53 tests, `tests/fake_ring.py`). The app uses the sandbox token meanwhile |
| Suggestion | Let the sandbox/Playground account complete the Connect step with the Playground Device, or exempt developer test accounts from the subscription check, and say in the Connect UI *why* an account can't connect |

## Medium

### F5 · No documented "package" event or full `subType` list, though the simulator has a Package button

| Field | Detail |
|-|-|
| Task | Handle package deliveries (the main use case) from webhooks |
| Steps | Read the Notifications event-type table, the API reference webhook section and the Motion Detection notification page; compared with the Playground buttons (Package / Vehicle / Motion) |
| Expected vs actual | **Expected:** a documented event type or `subType` for package, and one consistent payload shape. **Actual:** event types are `motion_detected` (subType `motion`, `human`, `vehicle`, `other_motion`), `button_press`, plus device/subscription/integration events; nothing mentions package. The `subType` field appears at `data.subType` on the Motion Detection page but is missing from the API reference's v1.1 example. It is also the only camelCase field in an otherwise snake_case payload |
| Severity | **Medium.** You can't know what a package event will look like, so the handler has to guess |
| Workaround | Normalised events defensively (`data.subType` if present, else `motion`), treated "package" as an internal type, and detect parcels from the video instead (YOLO-World "cardboard box") |
| Suggestion | Publish the complete `subType` list (including any package or delivery value), use one example payload across all pages, and state which Playground actions map to which event |

### F10 · Sandbox WHEP stream sometimes ends after ~10–15 s with no signal

| Field | Detail |
|-|-|
| Task | Capture one frame per second for up to 20 s from the Playground Device over WHEP |
| Steps | aiortc, video-only `recvonly` offer → `POST /v1/devices/{id}/media/streaming/whep/sessions` (201 + `Location`) → read frames → `DELETE` the session URL. Repeated over about 8 sessions |
| Expected vs actual | **Expected:** up to 30 s (battery) or 60 s (wired), per the docs. **Actual:** some sessions delivered ~20 s, but others stopped sending frames after ~10–15 s, with no error, connection-state change or RTCP BYE. The first decodable frame took 2.4–5.1 s to arrive. The first session after clicking a Playground button usually stalled within ~1 s. The docs don't say whether the Playground Device counts as battery or wired |
| Severity | **Medium.** Without an end-of-stream signal, capture code has to rely on timeouts |
| Workaround | Start the capture window at the first decoded frame; stop after 3 s without frames; retry once after 3 s if fewer than 3 frames arrived; always `DELETE` the session |
| Suggestion | Document the sandbox stream length and the device's power type, signal end-of-stream explicitly, and note that the stream switches clips when a Playground button is clicked |

### F11 · One-way apps can't disconnect a user from the partner side

| Field | Detail |
|-|-|
| Task | Implement a working **Disconnect** button on the App Homepage URL |
| Steps | Read the App Integrations API page (POST / PATCH / DELETE) |
| Expected vs actual | **Expected:** a partner-side call that unlinks the user and revokes tokens. **Actual:** `DELETE /v1/accounts/me/app-integrations` is available only to partner-initiated OAuth apps; one-way apps get `403`. A one-way app can only pause (`PATCH status: awaiting`) and delete its own copy of the tokens. Full removal has to happen in the Ring app, or via the console's **Disconnect all**, which disconnects every account |
| Severity | **Medium.** A user who clicks "Disconnect" expects access to be revoked, and the partner can't do that |
| Workaround | Disconnect pauses the integration, deletes DoorSight's tokens and tells the user to remove DoorSight in the Ring app. The `app_integration_removed` webhook deletes tokens if they remove it there first |
| Suggestion | Allow DELETE for one-way apps (or explain the design choice in the App Homepage guidance), and add per-account disconnect to the private app's Connect step |

### F4 · Sandbox has one doorbell, no sensors or chime, empty event history

| Field | Detail |
|-|-|
| Task | Test sensor and chime scopes, and event-history based features |
| Steps | Enabled the scopes, called `GET /v1/devices?include=status,capabilities` with the sandbox token, checked event history |
| Expected vs actual | **Expected:** at least one simulated device per scope and some seeded history. **Actual:** one doorbell ("Playground Device", DoorbellPro); no sensors, no chime; event history empty |
| Severity | **Medium.** Sensor and chime scopes, and anything built on event history, can't be tested |
| Workaround | Limited DoorSight to the doorbell; built our own event history in DynamoDB |
| Suggestion | Add a simulated contact sensor and chime to the sandbox, and seed a few days of event history (including the clips the Playground can play) |

### F1 · Getting Started requires a physical device; the console offers a sandbox

| Field | Detail |
|-|-|
| Task | Decide whether a Ring device must be bought before starting |
| Steps | Read Getting Started → Requirements, then the FAQ, then opened the developer console |
| Expected vs actual | **Expected:** one consistent answer. **Actual:** Getting Started lists "At least one Ring device for testing (camera or doorbell)"; the console and FAQ offer a sandbox with a simulated doorbell that is enough to build against |
| Severity | **Medium.** It can stop a developer from starting at all, or make them buy hardware they don't need |
| Workaround | Built everything against the sandbox's Playground Device |
| Suggestion | Make the sandbox the default path in Getting Started and list a physical device as optional (needed only for real-world testing) |

## Low

### F6 · Token Exchange URL request/response contract is unspecified

| Field | Detail |
|-|-|
| Task | Implement the Token Exchange URL (`POST /token`) |
| Steps | Read the Access Tokens, Configure and App Deployment pages |
| Expected vs actual | **Expected:** a contract listing the form fields Ring sends, the status/body it expects back, and whether it retries. **Actual:** the docs say Ring POSTs a form-encoded `code`, which must be exchanged within 60 s. Other fields, the expected response and retry behaviour aren't stated; only the reference implementation returns `{"status": "ok"}` |
| Severity | Low |
| Workaround | Read `code` only, log the received field *names* (never values), return `200 {"status":"ok"}` on success, `400` if `code` is missing, `502` if the exchange fails |
| Suggestion | Add a short contract table for the Token Exchange URL: fields, expected response, timeout, retries |

### F7 · `GET /v1/devices` returns undocumented fields

| Field | Detail |
|-|-|
| Task | Parse the device list |
| Steps | `GET /v1/devices?include=status,capabilities` with the sandbox token |
| Expected vs actual | **Expected:** the fields in Device Discovery (`attributes.name`, status `online`, capabilities `video` / `motion_detection` / `image_enhancements`). **Actual:** also `attributes.image_url`; `snapshot` under `image_enhancements`; `flood_detection`, `smoke_detection`, `glass_break_detection`, `co_detection_listener`, `contact_detection`, `battery_status` (all `null`); status `audio.snooze`, `state`, `reported_at` |
| Severity | Low |
| Workaround | Parse defensively and ignore unknown keys |
| Suggestion | Keep the documented response schema in sync with the live API, or publish an OpenAPI spec |

### F8 · HMAC key looks Base64-encoded but must be used as raw text

| Field | Detail |
|-|-|
| Task | Verify webhook signatures and account-linking nonces |
| Steps | Copied the HMAC Signature Key from the console |
| Expected vs actual | The key ends in `=` and looks Base64-encoded, which invites decoding it first. The docs (correctly) say to use the string as UTF-8 bytes, but only on the Notifications and App Deployment pages, not next to the key |
| Severity | Low |
| Workaround | Used the key as-is; a unit test pins this (`tests/test_webhook_signature.py`) |
| Suggestion | Show "use as-is, do not Base64-decode" next to the key in the console |

### F2 · Private-app account-linking fields are easy to miss in the Configure docs

| Field | Detail |
|-|-|
| Task | Fill in the account-linking URLs for a private app |
| Steps | Followed the Configure → Account Linking section, then opened the private-app form |
| Expected vs actual | The section starts with public apps: Staging / Production tabs, OAuth Redirect URI and Default Redirect URL. The private-app form has one page with Account Link, App Homepage, Token Exchange and Webhook URLs. That matches a short table at the very end of the section, which is easy to miss |
| Severity | Low |
| Workaround | Mapped the fields by name |
| Suggestion | Put the private-app table first (or in a tab), with a screenshot of the form |

### F3 · "Connect your IDE" lists Claude Desktop but not Claude Code

| Field | Detail |
|-|-|
| Task | Connect the Ring Appstore MCP docs server to the development environment |
| Steps | Opened the console's **Connect your IDE** panel |
| Expected vs actual | The Ring site advertises Claude support, so expected setup steps for Claude Code; the panel lists Claude Desktop only |
| Severity | Low |
| Workaround | Added the ring-appstore MCP server to Claude Code by hand; it works there |
| Suggestion | Add Claude Code, plus a generic "any MCP client" snippet, to the panel |
