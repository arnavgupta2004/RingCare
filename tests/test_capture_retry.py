import asyncio

from backend.vision.capture import CaptureResult, capture_with_retry


def _fake_capture(frame_counts):
    """Fake capture that writes N dummy frames per call, N taken from frame_counts in order."""
    calls = []

    async def capture(token, device_id, out_dir):
        n = frame_counts[len(calls)]
        calls.append(n)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(n):
            p = out_dir / f"frame_{i:03d}.jpg"
            p.write_bytes(b"jpg-call%d" % len(calls))
            frames.append(p)
        return CaptureResult(device_id=device_id, out_dir=out_dir, frames=frames)

    return capture, calls


def _run(tmp_path, counts):
    capture, calls = _fake_capture(counts)
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    result = asyncio.run(
        capture_with_retry("tok", "dev", tmp_path / "evt", capture=capture, sleep=fake_sleep)
    )
    return result, calls, sleeps


def test_no_retry_when_enough_frames(tmp_path):
    result, calls, sleeps = _run(tmp_path, [3])
    assert calls == [3] and sleeps == []
    assert result.attempts == 1 and len(result.frames) == 3


def test_retries_once_after_3s_when_too_few_frames(tmp_path):
    result, calls, sleeps = _run(tmp_path, [1, 20])
    assert calls == [1, 20]
    assert sleeps == [3.0]
    assert result.attempts == 2 and len(result.frames) == 20
    assert sorted(p.name for p in (tmp_path / "evt").iterdir()) == [f"frame_{i:03d}.jpg" for i in range(20)]


def test_retries_only_once_even_if_retry_also_short(tmp_path):
    result, calls, sleeps = _run(tmp_path, [2, 1, 99])
    assert calls == [2, 1]  # no third attempt
    assert sleeps == [3.0]
    # retry was worse, so the first attempt's two frames are kept on disk
    assert result.attempts == 2 and len(result.frames) == 2
    assert all(p.read_bytes() == b"jpg-call1" for p in result.frames)
    assert not (tmp_path / "evt" / "attempt1").exists()
