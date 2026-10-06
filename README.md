# DoorSight

A Ring doorstep assistant for elderly and low-vision residents. Each Ring event (motion, package, vehicle) starts an agent that looks at the live video, works out what is actually at the door, remembers state across events, and sends accessible updates to the resident and a short digest to a remote caregiver.

> Work in progress — built for the Amazon Developer Hackathon (Ring track + AWS Builder challenge).

## Status

- [x] Step 1 — FastAPI backend with the Ring console endpoints, Amazon Vision API client, webhook HMAC verification
- [x] Step 2 — WHEP live-video frame capture (aiortc, 1 frame/s, up to 20 s)
- [ ] Step 3 — Object detection + scene description
- [ ] Step 4 — Doorstep state and package lifecycle
- [ ] Step 5 — Agent + caregiver alerts
- [ ] Step 6 — Resident and caregiver web views
- [ ] Step 7 — Full account linking flow
- [ ] Step 8 — Docs, architecture diagram, demo

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in RING_CLIENT_ID, RING_CLIENT_SECRET, RING_HMAC_KEY, RING_ACCESS_TOKEN
pytest
python scripts/list_devices.py --include
uvicorn backend.main:app --port 8000
```

Expose it publicly (the Ring console needs HTTPS):

```bash
ngrok http --url=<your-static-domain>.ngrok-free.app 8000
```

## Endpoints

| Route | Ring console field | Purpose |
|-|-|-|
| `GET /link` | Account Link URL | Validates `time` (10 min window) and matches the `nonce` (HMAC-SHA256, URL-safe Base64, no padding) against stored unclaimed tokens |
| `GET /home` | App Homepage URL | Connection status |
| `POST /token` | Token Exchange URL | Receives the form-encoded auth code, exchanges it at `https://oauth.ring.com/oauth/token`, looks up the Account ID via `GET /v1/users/me`, stores tokens in `data/tokens.json` |
| `POST /webhook` | Webhook URL | Verifies `X-Signature: sha256=<hex>` over the raw body, returns 200 immediately, logs the payload to `logs/webhooks.jsonl` |
| `POST /simulate-event` | — | Dev trigger (`{"event_type": "package"\|"vehicle"\|"motion", "wait": false}`). Builds a v1.1-shaped event and runs it through the same handler as `/webhook`, which starts a WHEP capture to `data/frames/<event_id>/` |
| `GET /health` | — | Liveness |

## Frame capture

`backend/vision/capture.py` opens a video-only (`recvonly`) WHEP session on the device, saves one JPEG per second for up to 20 s, then closes the peer connection and DELETEs the session. The sandbox token is re-read from `.env` on each capture, so a regenerated token works without a restart.

```bash
curl -X POST localhost:8000/simulate-event -H 'Content-Type: application/json' -d '{"event_type":"package","wait":true}'
```

## Credits

Ring sandbox live-view clips are stock footage licensed under CC BY 4.0 (full attribution to be added with the demo).

## License

MIT — see [LICENSE](LICENSE).
