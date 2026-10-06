"""WHEP live-video frame capture from a Ring device using aiortc.

Flow (per Ring WHEP docs):
  1. RTCPeerConnection with a STUN server; attach track/state handlers BEFORE the offer
  2. one video transceiver, direction=recvonly (no audio)
  3. createOffer + setLocalDescription (aiortc waits for ICE gathering to complete)
  4. POST the offer to /v1/devices/{id}/media/streaming/whep/sessions -> 201 + answer + Location
  5. setRemoteDescription(answer), receive frames, save ~1 JPEG per second
  6. close the peer connection and DELETE the Location URL
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

from backend.ring.client import RingClient

logger = logging.getLogger("vision.capture")

STUN_SERVER = "stun:stun.l.google.com:19302"
TRACK_TIMEOUT_S = 10.0
FRAME_TIMEOUT_S = 3.0
# Battery devices cap sessions at 30 s, wired at 60 s; stay well under both.
MAX_CAPTURE_S = 20.0


@dataclass
class CaptureResult:
    device_id: str
    out_dir: Path
    frames: list[Path] = field(default_factory=list)
    duration_s: float = 0.0
    frames_received: int = 0
    width: int | None = None
    height: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "out_dir": str(self.out_dir),
            "frame_count": len(self.frames),
            "frames": [str(p) for p in self.frames],
            "frames_received": self.frames_received,
            "duration_s": round(self.duration_s, 2),
            "resolution": f"{self.width}x{self.height}" if self.width else None,
            "error": self.error,
        }


async def capture_frames(
    access_token: str,
    device_id: str,
    out_dir: Path,
    max_seconds: float = MAX_CAPTURE_S,
    interval_s: float = 1.0,
) -> CaptureResult:
    max_seconds = min(max_seconds, MAX_CAPTURE_S)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = CaptureResult(device_id=device_id, out_dir=out_dir)

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(urls=STUN_SERVER)]))
    track_ready: asyncio.Future[MediaStreamTrack] = asyncio.get_running_loop().create_future()

    @pc.on("track")
    def on_track(track: MediaStreamTrack) -> None:
        logger.info("remote track received: %s", track.kind)
        if track.kind == "video" and not track_ready.done():
            track_ready.set_result(track)

    @pc.on("connectionstatechange")
    async def on_state() -> None:
        logger.info("webrtc connection state: %s", pc.connectionState)
        if pc.connectionState == "failed" and not track_ready.done():
            track_ready.set_exception(RuntimeError("WebRTC connection failed"))

    pc.addTransceiver("video", direction="recvonly")

    session_url: str | None = None
    started = time.monotonic()
    async with RingClient(access_token) as ring:
        try:
            await pc.setLocalDescription(await pc.createOffer())
            answer, session_url = await ring.start_whep_session(device_id, pc.localDescription.sdp)
            logger.info("WHEP session started")
            await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

            track = await asyncio.wait_for(track_ready, TRACK_TIMEOUT_S)
            # The first decodable frame can lag the track by a few seconds (waiting for a
            # keyframe), so the capture window starts at the first frame, not at the track.
            first_frame_at: float | None = None
            next_save = 0.0
            while first_frame_at is None or time.monotonic() - first_frame_at < max_seconds:
                try:
                    frame = await asyncio.wait_for(track.recv(), FRAME_TIMEOUT_S)
                except (MediaStreamError, asyncio.TimeoutError):
                    logger.info("stream ended or stalled after %.1fs of video",
                                time.monotonic() - (first_frame_at or started) - FRAME_TIMEOUT_S)
                    break
                now = time.monotonic()
                if first_frame_at is None:
                    first_frame_at = next_save = now
                    logger.info("first frame %.1fs after session start", now - started)
                result.frames_received += 1
                if now >= next_save:
                    path = out_dir / f"frame_{len(result.frames):03d}.jpg"
                    image = frame.to_image()
                    result.width, result.height = image.size
                    await asyncio.to_thread(image.save, path, "JPEG", quality=90)
                    result.frames.append(path)
                    next_save = now + interval_s  # no catch-up bursts after a stall
        except Exception as exc:  # recorded on the result; session is always closed below
            logger.error("capture failed: %s", exc)
            result.error = str(exc)
        finally:
            result.duration_s = time.monotonic() - started
            await pc.close()
            if session_url:
                await ring.stop_whep_session(session_url)
            logger.info(
                "capture done: %d frames saved (%d received) in %.1fs -> %s",
                len(result.frames), result.frames_received, result.duration_s, out_dir,
            )
    return result
