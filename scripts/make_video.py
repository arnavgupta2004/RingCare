"""Cut, caption and voice the recorded demo.

Reads docs/VIDEO_SCRIPT.md (narration + timing), video/timeline.json (actual scene starts in the
raw recording) and video/raw/demo_raw.webm (from scripts/record_demo.py). Writes:

    captions/demo.srt        captions timed to the narration
    video/demo_silent.mp4    1920x1080, burned-in captions, no audio
    video/demo_tts.mp4       the same, with a placeholder macOS `say` voiceover (replace with your own)

Needs an ffmpeg with libass (Homebrew: `brew install ffmpeg-full`); set FFMPEG to override.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.record_demo import load_scenes  # noqa: E402

FFMPEG = os.getenv("FFMPEG") or next((p for p in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", shutil.which("ffmpeg") or "")
                                      if p and Path(p).exists()), "ffmpeg")
FFPROBE = str(Path(FFMPEG).with_name("ffprobe"))
VOICE, BASE_RATE = "Samantha", 175  # words per minute for `say`
LEAD_S, TAIL_S = 0.4, 0.6  # narration starts 0.4 s into a scene and ends 0.6 s before the next
MAX_TOTAL_S = 175.0  # stay under 2:55
MAX_WORDS_PER_CAPTION = 12


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def duration(path: Path) -> float:
    return float(run(FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)))


def tts(text: str, out: Path, max_s: float) -> tuple[float, int]:
    """Speak `text` into out (AIFF); speed up only if it doesn't fit max_s. Returns (seconds, wpm)."""
    rate = BASE_RATE
    for _ in range(4):
        run("say", "-v", VOICE, "-r", str(rate), "-o", str(out), text)
        d = duration(out)
        if d <= max_s:
            return d, rate
        rate = int(rate * d / max_s) + 5
    return d, rate


def chunks(text: str) -> list[str]:
    """Split narration into caption-sized pieces at sentence/clause boundaries."""
    parts = re.split(r"(?<=[.?!:])\s+", text.strip())
    out: list[str] = []
    for part in parts:
        words = part.split()
        while len(words) > MAX_WORDS_PER_CAPTION:
            cut = MAX_WORDS_PER_CAPTION
            for i in range(MAX_WORDS_PER_CAPTION, 5, -1):  # prefer breaking after a comma
                if words[i - 1].endswith(","):
                    cut = i
                    break
            out.append(" ".join(words[:cut]))
            words = words[cut:]
        if words:
            out.append(" ".join(words))
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> int:
    scenes = load_scenes()
    timeline = json.loads((ROOT / "video" / "timeline.json").read_text())["scene_offsets_s"]
    raw = ROOT / "video" / "raw" / "demo_raw.webm"
    origin = timeline[scenes[0]["id"]]  # trim everything before the first scene
    starts = {s["id"]: timeline[s["id"]] - origin for s in scenes}
    ends = {s["id"]: (starts[scenes[i + 1]["id"]] if i + 1 < len(scenes) else s["end"] + (starts[s["id"]] - s["start"]))
            for i, s in enumerate(scenes)}
    total = ends[scenes[-1]["id"]]
    if total > MAX_TOTAL_S:
        print(f"warning: video would be {total:.1f}s (> {MAX_TOTAL_S}s)", file=sys.stderr)

    work = Path(tempfile.mkdtemp(prefix="doorsight-video-"))
    srt_lines, audio_inputs, filters = [], [], []
    index = 1
    for i, scene in enumerate(scenes):
        sid, start, end = scene["id"], starts[scene["id"]], ends[scene["id"]]
        window = end - start - LEAD_S - TAIL_S
        aiff = work / f"{i:02d}_{sid}.aiff"
        spoken, rate = tts(scene["narration"], aiff, window)
        print(f"{sid:9s} {start:6.1f}s–{end:6.1f}s  narration {spoken:4.1f}s of {window:4.1f}s  ({rate} wpm)")
        audio_inputs += ["-i", str(aiff)]
        filters.append(f"[{i}:a]adelay={int((start + LEAD_S) * 1000)}:all=1[a{i}]")
        # captions follow the speech: each chunk gets time in proportion to its word count
        pieces = chunks(scene["narration"])
        total_words = sum(len(p.split()) for p in pieces)
        t = start + LEAD_S
        for piece in pieces:
            d = spoken * len(piece.split()) / total_words
            srt_lines.append(f"{index}\n{srt_time(t)} --> {srt_time(min(t + d, end - 0.05))}\n{piece}\n")
            index += 1
            t += d

    srt = ROOT / "captions" / "demo.srt"
    srt.parent.mkdir(exist_ok=True)
    srt.write_text("\n".join(srt_lines))

    out_dir = ROOT / "video"
    silent, voiced = out_dir / "demo_silent.mp4", out_dir / "demo_tts.mp4"
    style = ("FontName=Helvetica,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
             "BorderStyle=4,Outline=0,Shadow=0,MarginV=10")
    srt_escaped = str(srt).replace(":", r"\:")
    run(FFMPEG, "-y", "-ss", f"{origin:.3f}", "-t", f"{total:.3f}", "-i", str(raw),
        "-vf", f"fps=30,scale=1920:1080,subtitles='{srt_escaped}':force_style='{style}'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-an",
        "-movflags", "+faststart", str(silent))

    narration = work / "narration.m4a"
    mix = ";".join(filters) + ";" + "".join(f"[a{i}]" for i in range(len(scenes))) + \
        f"amix=inputs={len(scenes)}:normalize=0,apad=whole_dur={total:.3f}[out]"
    run(FFMPEG, "-y", *audio_inputs, "-filter_complex", mix, "-map", "[out]", "-t", f"{total:.3f}",
        "-c:a", "aac", "-b:a", "160k", str(narration))
    run(FFMPEG, "-y", "-i", str(silent), "-i", str(narration), "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(voiced))

    for f in (silent, voiced):
        print(f"{f.relative_to(ROOT)}: {duration(f):.1f}s, {f.stat().st_size / 1e6:.1f} MB")
    print(f"{srt.relative_to(ROOT)}: {index - 1} captions")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
