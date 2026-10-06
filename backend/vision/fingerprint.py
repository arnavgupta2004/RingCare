"""Compact camera-view fingerprints, to tell whether two captures show the same scene.

A package may only be marked missing by a capture of the same view it arrived in.
Each frame is reduced to a 63-bit perceptual hash (DCT of a 32x32 grayscale thumbnail)
plus a 32-bin grayscale histogram. Measured on the Ring sandbox clips:

    same view, different frames            pHash distance 0-2,  histogram correlation >= 0.996
    same view, package painted out         pHash distance 4,    histogram correlation  0.996
    different views (vehicle, bird feeder) pHash distance 28-36, histogram correlation <= 0.09

Both conditions must hold for a match; the thresholds sit well inside that gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_HASH_DISTANCE = 12
MIN_HIST_CORRELATION = 0.7
HIST_BINS = 32
SAMPLE_FRAMES = 3


def _gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return img


def _phash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    coeffs = cv2.dct(small)[:8, :8].flatten()[1:]  # drop the DC term -> 63 bits
    bits = coeffs > np.median(coeffs)
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def _hist(gray: np.ndarray) -> list[float]:
    h = cv2.calcHist([cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)], [0], None, [HIST_BINS], [0, 256])
    return [round(float(v), 5) for v in cv2.normalize(h, h).flatten()]


def frame_fingerprint(path: Path) -> dict[str, Any]:
    gray = _gray(path)
    return {"frame": path.name, "phash": _phash(gray), "hist": _hist(gray), "size": [gray.shape[1], gray.shape[0]]}


def view_fingerprint(frames: list[Path], device_id: str | None = None) -> dict[str, Any] | None:
    """Fingerprint a capture from up to 3 evenly spaced frames."""
    if not frames:
        return None
    n = len(frames)
    idx = sorted({0, n // 2, n - 1})[:SAMPLE_FRAMES]
    return {"version": 1, "device_id": device_id, "frames": [frame_fingerprint(frames[i]) for i in idx]}


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _correlation(a: list[float], b: list[float]) -> float:
    h1, h2 = np.array(a, np.float32), np.array(b, np.float32)
    return float(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL))


def compare_views(reference: dict[str, Any] | None, candidate: dict[str, Any] | None) -> dict[str, Any]:
    """Best match over all frame pairs. Returns {match, reason, hash_distance, hist_correlation}."""
    if not reference or not candidate:
        return {"match": False, "reason": "no reference view" if not reference else "no view for this capture",
                "hash_distance": None, "hist_correlation": None}
    ref_dev, cand_dev = reference.get("device_id"), candidate.get("device_id")
    if ref_dev and cand_dev and ref_dev != cand_dev:
        return {"match": False, "reason": "different device", "hash_distance": None, "hist_correlation": None}

    best_dist, best_corr = 64, -1.0
    for r in reference["frames"]:
        for c in candidate["frames"]:
            if r["size"] != c["size"]:
                continue
            best_dist = min(best_dist, _hamming(r["phash"], c["phash"]))
            best_corr = max(best_corr, _correlation(r["hist"], c["hist"]))
    match = best_dist <= MAX_HASH_DISTANCE and best_corr >= MIN_HIST_CORRELATION
    return {
        "match": match,
        "reason": "same view" if match else "different view",
        "hash_distance": best_dist,
        "hist_correlation": round(best_corr, 3),
    }
