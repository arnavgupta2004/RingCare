import cv2
import numpy as np
import pytest

from backend.vision.fingerprint import compare_views, view_fingerprint


def _scene(seed, size=(720, 1280)):
    """A textured synthetic 'camera view': smooth gradients plus a few large shapes."""
    rng = np.random.default_rng(seed)
    h, w = size
    y, x = np.mgrid[0:h, 0:w]
    img = (np.sin(x / rng.uniform(40, 200)) * 60 + np.cos(y / rng.uniform(40, 200)) * 60 + 120).astype(np.float32)
    for _ in range(6):
        x0, y0 = int(rng.uniform(0, w * 0.8)), int(rng.uniform(0, h * 0.8))
        cv2.rectangle(img, (x0, y0), (x0 + int(rng.uniform(80, 400)), y0 + int(rng.uniform(80, 300))),
                      float(rng.uniform(0, 255)), -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def _save(tmp_path, name, imgs):
    paths = []
    for i, img in enumerate(imgs):
        p = tmp_path / f"{name}_{i:03d}.jpg"
        cv2.imwrite(str(p), img)
        paths.append(p)
    return paths


@pytest.fixture
def views(tmp_path):
    door = _scene(1)
    noisy = [np.clip(door.astype(int) + np.random.default_rng(i).integers(-6, 7, door.shape), 0, 255).astype(np.uint8)
             for i in range(3)]
    with_box = door.copy()
    cv2.rectangle(with_box, (420, 480), (640, 720), 200, -1)  # ~6% of the frame, like the sandbox parcel
    return {
        "arrival": view_fingerprint(_save(tmp_path, "arrival", [with_box] * 3)),
        "same_view_frames": view_fingerprint(_save(tmp_path, "same", noisy)),
        "package_removed": view_fingerprint(_save(tmp_path, "removed", [door])),
        "other_camera": view_fingerprint(_save(tmp_path, "other", [_scene(2), _scene(3)])),
    }


def test_fingerprint_shape(views):
    fp = views["arrival"]
    assert fp["version"] == 1 and len(fp["frames"]) == 3  # first, middle, last
    f = fp["frames"][0]
    assert len(f["phash"]) == 16 and len(f["hist"]) == 32 and f["size"] == [1280, 720]


def test_same_view_matches_even_when_package_is_gone(views):
    for key in ("same_view_frames", "package_removed"):
        result = compare_views(views["arrival"], views[key])
        assert result["match"], (key, result)
        assert result["reason"] == "same view"


def test_different_camera_does_not_match(views):
    result = compare_views(views["arrival"], views["other_camera"])
    assert not result["match"] and result["reason"] == "different view"
    assert result["hash_distance"] > 12


def test_missing_reference_or_candidate_never_matches(views):
    assert compare_views(None, views["arrival"])["reason"] == "no reference view"
    assert compare_views(views["arrival"], None)["reason"] == "no view for this capture"


def test_device_mismatch_short_circuits(views):
    a = dict(views["arrival"], device_id="dev-a")
    b = dict(views["same_view_frames"], device_id="dev-b")
    assert compare_views(a, b)["reason"] == "different device"
    assert compare_views(a, dict(b, device_id="dev-a"))["match"]
