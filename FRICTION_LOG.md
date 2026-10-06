# Friction Log — Ring AppStore / Amazon Vision API

Running log of developer-experience friction found while building DoorSight.

Format: **task | steps | expected vs actual | severity | workaround | suggestion**

Severity: High = blocks progress, Medium = costs real time or risks a wrong implementation, Low = cosmetic or confusing.

---

### 1. Physical device requirement vs sandbox

| Field | Detail |
|-|-|
| Task | Decide whether a Ring device must be bought before building |
| Steps | Read the Getting Started page, then the FAQ, then opened the developer console |
| Expected vs actual | Expected one consistent answer. Getting Started says a physical Ring device is required; the FAQ and console offer a sandbox with a simulated doorbell |
| Severity | Medium |
| Workaround | Used the console sandbox ("Playground Device") |
| Suggestion | Update Getting Started to present the sandbox as the default path and a physical device as optional |

### 2. Configure docs don't match the private-app form

| Field | Detail |
|-|-|
| Task | Fill in account-linking URLs for a private app |
| Steps | Followed the Configure docs, then opened the private-app account linking form |
| Expected vs actual | Docs describe a "Default Redirect URL" and Staging / Production tabs; the private-app form shows "App Homepage URL" and a single page with no tabs |
| Severity | Low |
| Workaround | Mapped fields by name (Account Link URL, App Homepage URL, Token Exchange URL, Webhook URL) |
| Suggestion | Show a screenshot or a field table for the private-app form specifically |

### 3. "Connect your IDE" doesn't list Claude Code

| Field | Detail |
|-|-|
| Task | Connect the docs MCP server to the development environment |
| Steps | Opened the console "Connect your IDE" panel |
| Expected vs actual | The Ring homepage advertises Claude support, so expected setup steps for Claude Code; the panel lists Claude Desktop only |
| Severity | Low |
| Workaround | Added the MCP server manually using the generic configuration |
| Suggestion | Add Claude Code (and a generic "any MCP client" snippet) to the list |

### 4. Sandbox only offers one doorbell

| Field | Detail |
|-|-|
| Task | Test sensor and chime scopes |
| Steps | Enabled scopes, called `GET /v1/devices` with the sandbox token, checked event history |
| Expected vs actual | Expected sample devices for each scope. The sandbox has one doorbell ("Playground Device", DoorbellPro), no sensors or chime, and empty event history |
| Severity | Medium |
| Workaround | Limited scope to cameras/doorbells; webhook and event handling tested with synthetic payloads |
| Suggestion | Add simulated sensors and a chime to the sandbox, plus seeded event history |

### 5. No "package" webhook event type, but the simulator has a Package button

| Field | Detail |
|-|-|
| Task | Handle package deliveries from webhooks |
| Steps | Searched the Notifications docs and API reference event-type tables; compared with the console simulator (Package / Vehicle / Motion) |
| Expected vs actual | Expected a documented event type or `subType` for package. The documented types are `motion_detected` (subType e.g. `motion`, `human`, `vehicle`), `button_press`, device and subscription events. Nothing mentions package, and nothing says whether simulator clicks send webhooks at all |
| Severity | Medium |
| Workaround | Log every verified webhook in full and inspect what the simulator actually sends |
| Suggestion | Document the full `subType` list and state which simulator actions produce webhooks and with what payload |

### 6. What the Token Exchange URL should return is unspecified

| Field | Detail |
|-|-|
| Task | Implement `POST /token` |
| Steps | Read Access Tokens, Configure and App Deployment docs |
| Expected vs actual | Docs say Ring POSTs the code form-encoded (`code` field) and the partner must exchange it within 60 s. They don't list the other form fields Ring sends, the response status/body Ring expects, or whether Ring retries on failure. Only the reference implementation hints at returning `{"status": "ok"}` |
| Severity | Low |
| Workaround | Read `code` only, log the field names received, return `200 {"status":"ok"}` on success and `502` on failure |
| Suggestion | Add a request/response contract for the Token Exchange URL (fields, expected status, retry behaviour) |

### 7. `GET /v1/devices` returns undocumented fields

| Field | Detail |
|-|-|
| Task | Parse the device list |
| Steps | Called `GET /v1/devices?include=status,capabilities` with the sandbox token |
| Expected vs actual | Device Discovery docs list `attributes.name` only. The live response also returns `attributes.image_url`; capabilities include `snapshot` under `image_enhancements` plus `flood_detection`, `smoke_detection`, `glass_break_detection`, `co_detection_listener`, `battery_status` etc. as `null`; status includes `audio.snooze`, `state` and `reported_at` |
| Severity | Low |
| Workaround | Parse defensively and ignore unknown keys |
| Suggestion | Keep the response schema in the docs in sync with the live API |

### 8. HMAC key looks like Base64 but must be used as raw text

| Field | Detail |
|-|-|
| Task | Verify webhook signatures |
| Steps | Copied the HMAC Signature Key from the console |
| Expected vs actual | The key ends in `=` and looks Base64-encoded, which invites decoding it first. The docs (correctly) say to use the string as UTF-8 bytes, but this is easy to miss |
| Severity | Low |
| Workaround | Used the key as-is; added a unit test that pins this behaviour |
| Suggestion | Show this note next to the key in the console, not only in the docs |
