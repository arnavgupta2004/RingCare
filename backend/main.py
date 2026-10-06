"""DoorSight backend — Ring console endpoints (/link, /home, /token, /webhook) + /health.

Run:  uvicorn backend.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from datetime import datetime as _dt
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import auth
from backend.agent.runner import get_runner, select_brain_at_startup
from backend.config import get_settings
from backend.doorstep import PackageStateError, get_doorstep
from backend.events import SIMULATABLE, build_simulated_payload, handle_event, normalize
from backend.ring.accounts import LinkError, get_accounts, token_label
from backend.ring.client import RingAPIError
from backend.ring.signatures import SIGNATURE_HEADER, check_link_time, verify_webhook_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("doorsight")

settings = get_settings()  # fails fast with a clear message if .env is incomplete
login_throttle = auth.LoginThrottle()
TOKEN_REFRESH_CHECK_S = float(os.getenv("TOKEN_REFRESH_CHECK_S", "600"))
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


async def _token_refresh_loop() -> None:
    """Refresh linked-account tokens before they expire (and at least daily)."""
    while True:
        await asyncio.sleep(TOKEN_REFRESH_CHECK_S)
        try:
            await get_accounts().refresh_due()
        except Exception as exc:  # keep the loop alive
            logger.error("token refresh check failed: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_reminder_loop())
    refresh_task = asyncio.create_task(_token_refresh_loop())
    # Choose the agent brain (runs scripts/check_bedrock.sh when AGENT_BRAIN=auto) without
    # delaying startup; events use the rules brain until the choice is made.
    brain_task = asyncio.create_task(asyncio.to_thread(select_brain_at_startup))
    yield
    task.cancel()
    refresh_task.cancel()
    brain_task.cancel()


app = FastAPI(title="DoorSight", version="0.1.0", lifespan=lifespan)

# Captured frames for the web UI (snapshots). Only the frames directory is exposed.
(settings.data_dir / "frames").mkdir(parents=True, exist_ok=True)
app.mount("/media/frames", StaticFiles(directory=settings.data_dir / "frames"), name="frames")


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;font-size:1.25rem;line-height:1.5;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#fff;color:#111}}
h1{{font-size:2rem}} .ok{{color:#0a6b2d}} .bad{{color:#a00}} code{{font-size:1rem;word-break:break-all}}
input{{font:inherit;padding:.4rem;width:100%;max-width:22rem;border:2px solid #333;border-radius:6px}}
button{{font:inherit;padding:.5rem 1.1rem;margin:.25rem 0;border-radius:8px;border:2px solid #1f5fbf;background:#1f5fbf;color:#fff;cursor:pointer}}
button.danger{{background:#fff;color:#a00;border-color:#a00}} button.link{{background:none;color:#1f5fbf;border:none;padding:0;text-decoration:underline}}
.acct{{border:2px solid #ccc;border-radius:10px;padding:.75rem 1rem;margin:1rem 0}} th{{text-align:left;padding-right:1rem;vertical-align:top;font-weight:600}}
:focus-visible{{outline:3px solid #1f5fbf;outline-offset:2px}}</style>
</head><body><main>{body}</main></body></html>""",
        status_code=status_code,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Account linking ------------------------------------------------------


def _session_user(request: Request) -> str | None:
    return auth.read_session(request.cookies.get(auth.COOKIE_NAME))


def _same_origin(request: Request) -> bool:
    """Reject cross-site form posts (the session cookie is also SameSite=Lax)."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    from urllib.parse import urlsplit

    return urlsplit(origin).netloc == request.headers.get("host", "")


def _set_session(response, request: Request, email: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE_NAME, auth.make_session(email), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=secure, path="/")


def _client_key(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")


def _sign_in_fields(error: str | None = None) -> str:
    if not auth.sign_in_configured():
        return ('<p class="bad" role="alert">Sign-in is not set up yet. Run '
                "<code>python scripts/set_demo_password.py</code> on the DoorSight server.</p>")
    err = f'<p class="bad" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""{err}
<p><label for="email">Email</label><br><input id="email" name="email" type="email" autocomplete="username" required
  value="{html.escape(auth.demo_user_email())}"></p>
<p><label for="password">Password</label><br><input id="password" name="password" type="password"
  autocomplete="current-password" required></p>"""


def _link_form(nonce: str, time_ms: str, user: str | None, error: str | None = None) -> HTMLResponse:
    hidden = (f'<input type="hidden" name="nonce" value="{html.escape(nonce)}">'
              f'<input type="hidden" name="time" value="{html.escape(time_ms)}">')
    if user:
        body = (f"<p>You are signed in to DoorSight as <strong>{html.escape(user)}</strong>.</p>"
                f'<form method="post" action="/link">{hidden}<button type="submit">Link my Ring account</button></form>')
    else:
        body = (f"<p>Sign in to DoorSight to link your Ring account.</p>"
                f'<form method="post" action="/link">{hidden}{_sign_in_fields(error)}'
                f'<button type="submit">Sign in and link</button></form>')
    return _page("Link DoorSight", f"<h1>Link your Ring account</h1>{body}",
                 status_code=401 if error else 200)


@app.get("/link", response_class=HTMLResponse)
async def link(request: Request, nonce: str | None = None, time: str | None = None) -> HTMLResponse:  # noqa: A002
    """Account Link URL. Ring redirects here with ?nonce=<b64url>&time=<epoch ms>.

    Freshness is checked first (600 s window); the user must sign in before the nonce is matched.
    """
    if not nonce or not time:
        return _page("Link DoorSight", '<h1 class="bad">Missing nonce or time</h1>'
                     "<p>Start account linking from the Ring app.</p>", status_code=400)
    err = check_link_time(time, _now_ms())
    if err:
        logger.warning("link rejected: %s", err)
        return _page("Link DoorSight", f'<h1 class="bad">Link request rejected</h1><p>{html.escape(err)}. '
                     "Please start again from the Ring app.</p>", status_code=400)
    return _link_form(nonce, time, _session_user(request))


@app.post("/link", response_class=HTMLResponse)
async def link_submit(request: Request) -> HTMLResponse:
    if not _same_origin(request):
        return _page("Link DoorSight", '<h1 class="bad">Request blocked</h1>', status_code=403)
    form = await request.form()
    nonce, time_ms = str(form.get("nonce", "")), str(form.get("time", ""))
    if not nonce or not time_ms:
        return _page("Link DoorSight", '<h1 class="bad">Missing nonce or time</h1>', status_code=400)

    user = _session_user(request)
    if not user:
        key = _client_key(request)
        if login_throttle.blocked(key):
            return _link_form(nonce, time_ms, None, "Too many attempts. Please wait a few minutes and try again.")
        if not auth.check_credentials(str(form.get("email", "")), str(form.get("password", ""))):
            login_throttle.fail(key)
            logger.warning("link: failed sign-in")
            return _link_form(nonce, time_ms, None, "That email and password don't match.")
        login_throttle.reset(key)
        user = auth.demo_user_email()

    try:
        account = await get_accounts().complete_link(nonce, time_ms, user)
    except LinkError as exc:
        response = _page("Link DoorSight", f'<h1 class="bad">Could not link</h1><p>{html.escape(str(exc))}</p>',
                         status_code=400)
    else:
        done = account.integration_status == "completed"
        response = _page("Linked", '<h1 class="ok">Your Ring account is linked</h1>'
                         f"<p>Ring account <code>{html.escape(account.account_id)}</code> is connected to "
                         f"DoorSight as {html.escape(account.account_identifier or user)}.</p>"
                         + ("" if done else '<p class="bad">Setup is not finished yet; use “Finish setup” on the '
                                            "home page.</p>")
                         + '<p><a href="/home">Go to DoorSight home</a></p>')
    _set_session(response, request, user)
    return response


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    """Token Exchange URL. Ring POSTs the auth code as application/x-www-form-urlencoded."""
    form = await request.form()
    code = form.get("code")
    logger.info("/token called with fields: %s", sorted(form.keys()))  # field names only, never values
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    try:
        await get_accounts().receive_code(str(code))
    except RingAPIError as exc:
        logger.error("/token failed: %s", exc)
        return JSONResponse({"error": "token exchange failed"}, status_code=502)
    return JSONResponse({"status": "ok"})


@app.post("/signin")
async def signin(request: Request):
    if not _same_origin(request):
        return _page("DoorSight", '<h1 class="bad">Request blocked</h1>', status_code=403)
    form = await request.form()
    key = _client_key(request)
    if login_throttle.blocked(key) or not auth.check_credentials(str(form.get("email", "")),
                                                                 str(form.get("password", ""))):
        login_throttle.fail(key)
        return await home(request, error="That email and password don't match (or too many attempts).")
    login_throttle.reset(key)
    response = RedirectResponse("/home", status_code=303)
    _set_session(response, request, auth.demo_user_email())
    return response


@app.post("/signout")
async def signout(request: Request):
    response = RedirectResponse("/home", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


def _expiry(ts: float) -> str:
    if not ts:
        return "—"
    left = ts - time.time()
    when = datetime.fromtimestamp(ts).astimezone().strftime("%a %H:%M %Z")
    if left <= 0:
        return f"expired ({when})"
    return f"{when} (in {int(left // 3600)} h {int(left % 3600 // 60)} min)"


STATUS_TEXT = {"linked": "Linked", "unclaimed": "Waiting for sign-in", "needs_relink": "Needs re-linking",
               "disconnected": "Disconnected by DoorSight", "removed": "Removed in the Ring app"}


@app.get("/home", response_class=HTMLResponse)
async def home(request: Request, error: str | None = None, notice: str | None = None) -> HTMLResponse:
    """App Homepage URL: connection status, account details and Disconnect (signed in)."""
    accounts = get_accounts()
    user = _session_user(request)
    all_accounts = accounts.store.list_accounts()
    active = accounts.active_account()
    status_line = (f'<p class="ok">Ring: connected ({len([a for a in all_accounts if a.status == "linked"])} '
                   "linked account).</p>" if active else '<p class="bad">Ring: no account linked yet.</p>')
    sandbox = ("<p>Developer sandbox token: configured — used for API calls when no account is linked.</p>"
               if (accounts.sandbox_token() or "").strip() else "")
    source = token_label(active.account_id) if active else ("sandbox" if sandbox else "none")
    parts = [f"<h1>DoorSight</h1>{status_line}"]
    if notice:
        parts.append(f'<p class="ok" role="status">{html.escape(notice)}</p>')
    if error:
        parts.append(f'<p class="bad" role="alert">{html.escape(error)}</p>')

    if not user:
        parts.append('<h2>Sign in</h2><p>Sign in to see account details or disconnect.</p>'
                     f'<form method="post" action="/signin">{_sign_in_fields()}<button type="submit">Sign in</button></form>')
    else:
        parts.append(f'<p>Signed in as <strong>{html.escape(user)}</strong>. API calls use the '
                     f'<strong>{html.escape(source)}</strong> token.</p>')
        shown = [a for a in all_accounts if a.status != "unclaimed" or a.access_token]
        if not shown:
            parts.append("<p>No Ring accounts yet. Connect one from the Ring developer console (Connect step).</p>")
        for a in reversed(shown):
            rows = [("Status", STATUS_TEXT.get(a.status, a.status)),
                    ("Ring account ID", f"<code>{html.escape(a.account_id)}</code>"),
                    ("Linked as", html.escape(a.account_identifier or "—")),
                    ("Ring integration", html.escape(a.integration_status or "—")),
                    ("Access token expires", html.escape(_expiry(a.expires_at)) if a.access_token else "—"),
                    ("Last refreshed", html.escape(datetime.fromtimestamp(a.refreshed_at).astimezone()
                                                   .strftime("%a %H:%M") if a.refreshed_at else "—"))]
            if a.last_error:
                rows.append(("Last error", html.escape(a.last_error)))
            table = "".join(f"<tr><th scope='row'>{k}</th><td>{v}</td></tr>" for k, v in rows)
            actions = ""
            if a.status == "linked":
                aid = html.escape(a.account_id)
                if a.integration_status != "completed":
                    actions += (f'<form method="post" action="/home/complete"><input type="hidden" name="account_id" '
                                f'value="{aid}"><button type="submit">Finish setup</button></form>')
                actions += (f'<form method="post" action="/home/disconnect"><input type="hidden" name="account_id" '
                            f'value="{aid}"><button type="submit" class="danger">Disconnect</button></form>')
            parts.append(f'<section class="acct"><table>{table}</table>{actions}</section>')
        parts.append('<form method="post" action="/signout"><button type="submit" class="link">Sign out</button></form>')

    parts.append(sandbox)
    parts.append(f"<p>Last webhook: {html.escape(_last_webhook_summary())}</p>")
    return _page("DoorSight", "".join(parts), status_code=401 if error else 200)


async def _signed_in_post(request: Request) -> tuple[str | None, dict]:
    if not _same_origin(request):
        raise HTTPException(403, "cross-site request blocked")
    user = _session_user(request)
    if not user:
        raise HTTPException(401, "sign in first")
    return user, dict(await request.form())


@app.post("/home/disconnect", response_class=HTMLResponse)
async def disconnect(request: Request) -> HTMLResponse:
    _, form = await _signed_in_post(request)
    try:
        result = await get_accounts().disconnect(str(form.get("account_id", "")))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    note = ("Disconnected. DoorSight deleted its Ring tokens"
            + (" and paused the integration in Ring." if result["ring_integration_paused"] else ".")
            + " To remove DoorSight from Ring completely, remove it in the Ring app"
              " (or use “Disconnect all” in the Ring developer console).")
    return await home(request, notice=note)


@app.post("/home/complete", response_class=HTMLResponse)
async def complete(request: Request) -> HTMLResponse:
    _, form = await _signed_in_post(request)
    accounts = get_accounts()
    account = accounts.store.get_account(str(form.get("account_id", "")))
    if account is None or account.status != "linked":
        raise HTTPException(404, "no linked account")
    ok = await accounts.finish_integration(account)
    return await home(request, notice="Setup finished." if ok else None,
                      error=None if ok else "Ring did not accept the update; try again shortly.")


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


# Real sandbox captures recorded earlier (frames from the Ring WHEP stream), replayed for demos.
REPLAY_CAPTURES = {
    "package": "sim-package-1791250581919",
    "vehicle": "sim-vehicle-1791250656310",
    "motion": "sim-motion-1791250093388",
}


class ReplayRequest(BaseModel):
    event_type: str
    capture: str | None = None  # a data/frames/<capture> directory; default per event type


@app.post("/demo/replay")
async def demo_replay(body: ReplayRequest) -> dict[str, Any]:
    """Dev/demo trigger: run a previously captured real sandbox clip through the agent.

    Same path as a live event (agent, tools, guard rails, notifications), but the frames come
    from data/frames/<capture> instead of a new WHEP session. The event is marked source="replay".
    """
    from backend.vision.analyze import analysis_path, analyze_event

    capture = body.capture or REPLAY_CAPTURES.get(body.event_type)
    if body.event_type not in SIMULATABLE or not capture:
        raise HTTPException(422, f"event_type must be one of {list(SIMULATABLE)}")
    frames_dir = settings.data_dir / "frames" / capture
    if not frames_dir.is_dir():
        raise HTTPException(404, f"no capture {capture}")
    path = analysis_path(capture)
    analysis = json.loads(path.read_text()) if path.exists() else await asyncio.to_thread(
        analyze_event, capture, body.event_type, frames_dir)
    event_id = f"replay-{body.event_type}-{_now_ms()}"
    ctx = await get_runner().handle_event(event_id, body.event_type, source="replay", preloaded_analysis=analysis)
    return {"event_id": event_id, "capture": capture, "brain": ctx.brain, "reason": ctx.reason,
            "package_action": ctx.package_action,
            "notifications": [{"audience": n.audience, "kind": n.kind, "text": n.text} for n in ctx.notifications]}


class DigestRequest(BaseModel):
    day: str | None = None  # "YYYY-MM-DD" home-local; default = the 24 hours ending now (sim)


@app.post("/digest")
async def digest(body: DigestRequest | None = None) -> dict[str, Any]:
    """Ask the agent to write and send the caregiver's daily digest."""
    ctx = await get_runner().write_digest((body.day if body else None) or None)
    note = next((n for n in ctx.notifications if n.kind == "daily_digest"), None)
    return {"brain": ctx.brain, "reason": ctx.reason, "notification": note.to_dict() if note else None,
            "trace": ctx.trace}


@app.get("/state")
async def state() -> dict[str, Any]:
    doorstep = get_doorstep()
    return {
        "clock": doorstep.clock.as_dict(),
        "packages": [p.to_dict() for p in doorstep.store.list_packages()],
        "notifications": [n.to_dict() for n in doorstep.store.list_notifications()],
        "events": [{**e.to_dict(), "snapshot_url": doorstep.snapshots.url(e.snapshot)}
                   for e in doorstep.store.list_events()[-50:]],
        "backends": {"state": type(doorstep.store).__name__, "snapshots": doorstep.snapshots.name,
                     "notifier": doorstep.notifier.name},
        "agent": get_runner().info(),
    }


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
