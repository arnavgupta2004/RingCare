"""DoorSight backend — Ring console endpoints (/link, /home, /token, /webhook) + /health.

Run:  uvicorn backend.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime as _dt
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import get_settings
from backend.doorstep import PackageStateError, get_doorstep
from backend.events import SIMULATABLE, build_simulated_payload, handle_event, normalize
from backend.ring.client import RingAPIError, RingClient, exchange_authorization_code
from backend.ring.signatures import SIGNATURE_HEADER, check_link_time, nonce_matches, verify_webhook_signature
from backend.token_store import TokenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("doorsight")

settings = get_settings()  # fails fast with a clear message if .env is incomplete
token_store = TokenStore(settings.tokens_file)
settings.logs_dir.mkdir(parents=True, exist_ok=True)

REMINDER_CHECK_INTERVAL_S = float(os.getenv("REMINDER_CHECK_INTERVAL_S", "60"))


async def _reminder_loop() -> None:
    """Promote overdue packages to "reminded" even when no new events arrive."""
    while True:
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_S)
        try:
            await asyncio.to_thread(get_doorstep().check_reminders)
        except Exception as exc:  # keep the loop alive
            logger.error("reminder check failed: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="DoorSight", version="0.1.0", lifespan=lifespan)

# Captured frames for the web UI (snapshots). Only the frames directory is exposed.
(settings.data_dir / "frames").mkdir(parents=True, exist_ok=True)
app.mount("/media/frames", StaticFiles(directory=settings.data_dir / "frames"), name="frames")


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;font-size:1.25rem;line-height:1.5;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#fff;color:#111}}
h1{{font-size:2rem}} .ok{{color:#0a6b2d}} .bad{{color:#a00}} code{{font-size:1rem;word-break:break-all}}</style>
</head><body><main>{body}</main></body></html>"""
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Account linking ------------------------------------------------------


@app.get("/link", response_class=HTMLResponse)
async def link(nonce: str | None = None, time: str | None = None) -> HTMLResponse:  # noqa: A002
    """Account Link URL. Ring redirects here with ?nonce=<b64url>&time=<epoch ms>.

    Validates freshness (600 s window) and matches the nonce against unclaimed
    tokens: nonce == Base64URL_NoPadding(HMAC-SHA256(key, "<time>:<account_id>")).
    The mandatory partner sign-in and the App-Integrations POST/PATCH are added in build step 7.
    """
    if not nonce or not time:
        return _page("Link DoorSight", '<h1 class="bad">Missing nonce or time</h1>'
                     "<p>Start account linking from the Ring app.</p>")

    err = check_link_time(time, _now_ms())
    if err:
        logger.warning("link rejected: %s", err)
        return _page("Link DoorSight", f'<h1 class="bad">Link request rejected</h1><p>{html.escape(err)}</p>')

    matched = None
    for account_id, record in token_store.all().items():
        if record.get("status") == "unclaimed" and nonce_matches(settings.ring_hmac_key, nonce, time, account_id):
            matched = account_id
            break

    if not matched:
        logger.warning("link: nonce did not match any unclaimed token")
        return _page("Link DoorSight", '<h1 class="bad">No matching Ring authorization</h1>'
                     "<p>We could not match this link request. Please retry from the Ring app.</p>")

    logger.info("link: nonce matched account %s", matched)
    return _page("Link DoorSight", '<h1 class="ok">Ring account recognised</h1>'
                 f"<p>Account <code>{html.escape(matched)}</code> is ready to link.</p>")


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    """Token Exchange URL. Ring POSTs the auth code as application/x-www-form-urlencoded."""
    form = await request.form()
    code = form.get("code")
    logger.info("/token called with fields: %s", sorted(form.keys()))
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)

    try:
        tokens = await exchange_authorization_code(str(code), settings.ring_client_id, settings.ring_client_secret)
        async with RingClient(tokens["access_token"]) as ring:
            me = await ring.get_user_me()
        account_id = me["data"]["id"]
    except (RingAPIError, KeyError, TypeError) as exc:
        logger.error("/token failed: %s", exc)
        return JSONResponse({"error": "token exchange failed"}, status_code=502)

    token_store.save_unclaimed(account_id, tokens)
    logger.info("/token stored unclaimed tokens for account %s", account_id)
    return JSONResponse({"status": "ok"})


@app.get("/home", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    """App Homepage URL: connection status."""
    records = token_store.all()
    if records:
        rows = "".join(
            f"<li><code>{html.escape(a)}</code> — {html.escape(r.get('status', '?'))}, access token "
            f"{'valid' if r.get('expires_at', 0) > time.time() else 'expired'}</li>"
            for a, r in records.items()
        )
        linked = f'<p class="ok">Linked Ring accounts:</p><ul>{rows}</ul>'
    else:
        linked = '<p class="bad">No Ring account linked yet.</p>'

    last = _last_webhook_summary()
    dev = "<p>Developer sandbox token: configured</p>" if settings.ring_access_token else ""
    return _page("DoorSight", f"<h1>DoorSight</h1>{linked}{dev}<p>Last webhook: {html.escape(last)}</p>")


# --- Webhooks -------------------------------------------------------------


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    raw = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)
    if not verify_webhook_signature(settings.ring_hmac_key, raw, signature):
        logger.warning("webhook rejected: %s signature (%d bytes)", "missing" if not signature else "bad", len(raw))
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    background.add_task(_process_webhook, raw, dict(request.headers))
    return JSONResponse({"status": "ok"})


async def _process_webhook(raw: bytes, headers: dict[str, str]) -> None:
    payload = _log_webhook(raw, headers)
    if isinstance(payload, dict):
        await handle_event(normalize(payload, source="webhook"))


class SimulateEventRequest(BaseModel):
    event_type: str
    device_id: str | None = None
    wait: bool = False  # true: block until capture finishes and return the result


@app.post("/simulate-event")
async def simulate_event(body: SimulateEventRequest, background: BackgroundTasks) -> JSONResponse:
    """Dev trigger: the sandbox simulator does not send webhooks, so build the same
    v1.1-shaped payload here and pass it through the same handler as /webhook.

    The Playground's Package/Vehicle/Motion buttons switch the clip the sandbox device
    streams, so click the matching button in the Ring Playground before calling this."""
    if body.event_type not in SIMULATABLE:
        return JSONResponse({"error": f"event_type must be one of {list(SIMULATABLE)}"}, status_code=422)
    event = normalize(build_simulated_payload(body.event_type, body.device_id), source="simulated")
    hint = f'click "{body.event_type.capitalize()}" in the Ring Playground first'
    logger.info("simulate-event %s: presenter hint: %s", body.event_type, hint)
    if body.wait:
        return JSONResponse({**await handle_event(event), "presenter_hint": hint})
    background.add_task(handle_event, event)
    return JSONResponse({"status": "accepted", "event_id": event.event_id, "presenter_hint": hint}, status_code=202)


# --- Doorstep state & demo clock -------------------------------------------


class ClockRequest(BaseModel):
    set: str | None = None  # ISO 8601; naive = home-local time, e.g. "2026-10-07T03:00"
    advance_hours: float = 0.0
    advance_seconds: float = 0.0
    reset: bool = False


@app.get("/demo/clock")
async def get_demo_clock() -> dict[str, Any]:
    return get_doorstep().clock.as_dict()


@app.post("/demo/clock")
async def set_demo_clock(body: ClockRequest) -> dict[str, Any]:
    """Set or advance simulated time, then run time-based rules (reminders)."""
    doorstep = get_doorstep()
    clock = doorstep.clock
    if body.reset:
        clock.reset()
    if body.set:
        try:
            clock.set(_dt.fromisoformat(body.set))
        except ValueError as exc:
            raise HTTPException(422, f"invalid 'set' time: {exc}") from exc
    if body.advance_hours or body.advance_seconds:
        clock.advance(body.advance_hours * 3600 + body.advance_seconds)
    reminders = await asyncio.to_thread(doorstep.check_reminders)
    return {**clock.as_dict(), "reminders_queued": [n.to_dict() for n in reminders]}


@app.post("/packages/{package_id}/picked-up")
async def package_picked_up(package_id: str) -> dict[str, Any]:
    """The resident's "I picked up the package" button."""
    try:
        pkg = await asyncio.to_thread(get_doorstep().mark_picked_up, package_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PackageStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return pkg.to_dict()


@app.get("/state")
async def state() -> dict[str, Any]:
    doorstep = get_doorstep()
    return {
        "clock": doorstep.clock.as_dict(),
        "packages": [p.to_dict() for p in doorstep.store.list_packages()],
        "notifications": [n.to_dict() for n in doorstep.store.list_notifications()],
        "events": [_event_json(e) for e in doorstep.store.list_events()[-50:]],
    }


def _event_json(e) -> dict[str, Any]:
    d = e.to_dict()
    snap = d.get("snapshot")
    d["snapshot_url"] = f"/media/{snap}" if snap and snap.startswith("frames/") else None
    return d


def _log_webhook(raw: bytes, headers: dict[str, str]) -> Any:
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
        "payload": payload,
    }
    with settings.webhook_log_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    event_type = payload.get("data", {}).get("type") if isinstance(payload, dict) else None
    logger.info("webhook logged: type=%s", event_type)
    return payload


def _last_webhook_summary() -> str:
    path = settings.webhook_log_file
    if not path.exists() or path.stat().st_size == 0:
        return "none received"
    last = path.read_text().strip().splitlines()[-1]
    try:
        entry = json.loads(last)
        etype = entry["payload"]["data"]["type"]
    except (ValueError, KeyError, TypeError):
        etype = "unknown"
    return f"{etype} at {entry.get('received_at', '?')}"


def _now_ms() -> int:
    return int(time.time() * 1000)
