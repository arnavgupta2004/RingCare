import json
from pathlib import Path

import pytest

from backend.vision.describe import DescribeError, describe_scene, parse_strict_json, pick_representative_frames

VALID = {
    "scene_summary": "A cardboard box sits on the front step.",
    "objects_present": ["cardboard package", "steps"],
    "package_visible": True,
    "vehicle_visible": False,
    "people_visible": False,
    "confidence": 0.9,
    "accessible_description": "A parcel is waiting on your front step.",
}


def test_parse_accepts_valid_json_and_code_fences():
    assert parse_strict_json(json.dumps(VALID)) == VALID
    assert parse_strict_json("```json\n" + json.dumps(VALID) + "\n```") == VALID


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("accessible_description"),
    lambda d: d.update(package_visible="yes"),
    lambda d: d.update(confidence=True),
    lambda d: d.update(objects_present="box"),
])
def test_parse_rejects_missing_or_mistyped_fields(mutate):
    data = dict(VALID)
    mutate(data)
    with pytest.raises(DescribeError):
        parse_strict_json(json.dumps(data))


def test_parse_rejects_non_json():
    with pytest.raises(DescribeError):
        parse_strict_json("A parcel is on the step.")


def test_pick_frames_first_last_and_most_detections():
    frames = [Path(f"frame_{i:03d}.jpg") for i in range(10)]
    det = {"frames": [{"index": i, "counts": {"vehicle": 5 if i == 6 else 1}} for i in range(10)]}
    assert [p.name for p in pick_representative_frames(frames, det)] == ["frame_000.jpg", "frame_006.jpg", "frame_009.jpg"]


def test_pick_frames_falls_back_to_middle_without_detections():
    frames = [Path(f"frame_{i:03d}.jpg") for i in range(10)]
    assert [p.name for p in pick_representative_frames(frames, None)] == ["frame_000.jpg", "frame_005.jpg", "frame_009.jpg"]


class FakeBedrock:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.replies.pop(0)}]}},
                "stopReason": "end_turn", "usage": {}}


@pytest.fixture
def frames(tmp_path):
    from PIL import Image
    paths = []
    for i in range(4):
        p = tmp_path / f"frame_{i:03d}.jpg"
        Image.new("RGB", (1280, 720), (i * 40, 80, 120)).save(p)
        paths.append(p)
    return paths


def test_describe_sends_three_images_and_returns_result(frames):
    fake = FakeBedrock([json.dumps(VALID)])
    out = describe_scene(frames, "package", None, model_id="m", client=fake)
    assert out["result"] == VALID
    content = fake.calls[0]["messages"][0]["content"]
    assert sum("image" in c for c in content) == 3
    assert out["frames_sent"] == ["frame_000.jpg", "frame_002.jpg", "frame_003.jpg"]


def test_describe_retries_once_on_invalid_json(frames):
    fake = FakeBedrock(["not json", json.dumps(VALID)])
    assert describe_scene(frames, "package", None, model_id="m", client=fake)["result"] == VALID
    assert len(fake.calls) == 2


def test_describe_gives_up_after_two_invalid_replies(frames):
    fake = FakeBedrock(["nope", "still nope"])
    with pytest.raises(DescribeError):
        describe_scene(frames, "package", None, model_id="m", client=fake)
