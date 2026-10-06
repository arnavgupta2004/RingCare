"""DoorSight backend — Ring console endpoints (/link, /home, /token, /webhook) + /health.

Run:  uvicorn backend.main:app --port 8000
"""

from __future__ import annotations

import html
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from backend.config import get_settings
from backend.ring.client import RingAPIError, RingClient, exchange_authorization_code
from backend.ring.signatures import SIGNATURE_HEADER, check_link_time, nonce_matches, verify_webhook_signature
from backend.token_store import TokenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("doorsight")

settings = get_settings()  # fails fast with a clear message if .env is incomplete
token_store = TokenStore(settings.tokens_file)
settings.logs_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DoorSight", version="0.1.0")


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

    background.add_task(_log_webhook, raw, dict(request.headers))
    return JSONResponse({"status": "ok"})


def _log_webhook(raw: bytes, headers: dict[str, str]) -> None:
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
