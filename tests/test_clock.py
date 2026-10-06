from datetime import datetime, timezone

import pytest

from backend.clock import DemoClock, parse_offset

REAL = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)  # 14:10 IST


def make_clock(offset=0.0):
    return DemoClock(offset_s=offset, tz="Asia/Kolkata", real_now=lambda: REAL)


@pytest.mark.parametrize("value,seconds", [
    ("0", 0), ("3h", 10800), ("-90m", -5400), ("1d", 86400), ("45s", 45), ("10800", 10800), ("", 0), (None, 0),
])
def test_parse_offset(value, seconds):
    assert parse_offset(value) == seconds


def test_parse_offset_rejects_garbage():
    with pytest.raises(ValueError):
        parse_offset("tomorrow")


def test_now_is_real_plus_offset_in_home_tz():
    clock = make_clock(parse_offset("3h"))
    assert clock.now().strftime("%H:%M %Z") == "17:10 IST"
    assert clock.real_now().strftime("%H:%M") == "14:10"


def test_set_naive_time_is_home_local_and_advance_and_reset():
    clock = make_clock()
    assert clock.set(datetime(2026, 10, 7, 3, 0)).strftime("%Y-%m-%d %H:%M") == "2026-10-07 03:00"
    assert clock.advance(90 * 60).strftime("%H:%M") == "04:30"
    assert clock.reset() == REAL.astimezone(clock.tz)
