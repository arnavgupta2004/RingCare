# Feature requests — Ring developer platform

Requests that came out of building DoorSight, most important first. Each links to the friction-log entry with full reproduction steps.

| # | Request | Priority |
|-|-|-|
| 1 | Playground actions deliver real signed webhooks | **Critical** |
| 2 | Account linking testable from the developer sandbox | **Critical** |
| 3 | A documented package / delivery event | **Important** |
| 4 | Partner-side disconnect for one-way apps | **Important** |
| 5 | A more complete, better-signalled sandbox | **Nice-to-have** |

---

### 1. Playground actions deliver real signed webhooks — Critical

**Problem:** clicking Package, Vehicle or Motion in the Playground switches the video clip, but no webhook reaches the configured Webhook URL ([F9](../FRICTION_LOG.md#f9--playground-simulator-buttons-dont-send-webhooks)). Webhooks are how a Ring integration learns that something happened, so the most important path can't be tested before certification.

**Request:**
- Each Playground action sends the matching HMAC-signed v1.1 webhook (e.g. `motion_detected` with `subType: vehicle`) to the app's Webhook URL.
- A **Send test webhook** button shows the response status and latency.

**Benefit:** developers test signature verification, retries and event handling end to end, without building a simulator of their own.

### 2. Account linking testable from the developer sandbox — Critical

**Problem:** the private app's Connect step stops before Ring calls the Token Exchange URL unless the Ring account has a Ring Protect plan or trial ([F12](../FRICTION_LOG.md#f12--live-account-linking-blocked-at-the-connect-step-ring-protect-plan-required)). The full one-way flow (token exchange, nonce, App-Integrations POST/PATCH) can't be verified live, even though certification depends on it.

**Request:**
- Let the Playground account complete Connect with the Playground Device, or exempt developer test accounts from the subscription check.
- Have the Connect UI say *why* an account can't connect.

**Benefit:** partners can verify the hardest part of the integration before submitting for certification.

### 3. A documented package / delivery event — Important

**Problem:** the Playground has a Package button, but no package event or `subType` is documented, and `subType` placement differs between doc pages ([F5](../FRICTION_LOG.md#f5--no-documented-package-event-or-full-subtype-list-though-the-simulator-has-a-package-button)).

**Request:**
- Publish the complete `subType` list, including a package or delivery value if Ring detects one.
- Use one canonical v1.1 example payload on every page.

**Benefit:** package-related apps (a large share of doorbell use cases) can react to deliveries directly instead of inferring them from video.

### 4. Partner-side disconnect for one-way apps — Important

**Problem:** `DELETE /v1/accounts/me/app-integrations` returns 403 for one-way apps, so a partner's "Disconnect" button can't revoke access. The console only offers **Disconnect all** ([F11](../FRICTION_LOG.md#f11--one-way-apps-cant-disconnect-a-user-from-the-partner-side)).

**Request:**
- Allow DELETE for one-way apps.
- Add per-account disconnect to the private app's Connect step.

**Benefit:** users get a disconnect that actually revokes access, which matters for privacy-sensitive apps like home monitoring.

### 5. A more complete, better-signalled sandbox — Nice-to-have

**Problem:** the sandbox has one doorbell, no sensors or chime, and empty event history ([F4](../FRICTION_LOG.md#f4--sandbox-has-one-doorbell-no-sensors-or-chime-empty-event-history)). WHEP streams sometimes stop after 10–15 s with no end-of-stream signal ([F10](../FRICTION_LOG.md#f10--sandbox-whep-stream-sometimes-ends-after-1015-s-with-no-signal)). The sandbox token lasts 30 minutes.

**Request:**
- Simulated contact sensor and chime.
- A few days of seeded event history.
- Documented stream length, plus an explicit end-of-stream signal.
- A refreshable sandbox token.

**Benefit:** sensor and chime scopes and history-based features become testable, and capture code doesn't need timeout heuristics.
