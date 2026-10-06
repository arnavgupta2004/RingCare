"""Demo clock: real time plus an adjustable offset, in the home's timezone.

All time-based doorstep logic (reminders, unusual-hour scoring, notification text) reads
`get_clock().now()`, so a demo can jump to 03:00 or skip ahead 3 hours without waiting.

Configuration:
    DEMO_TIME_OFFSET  initial offset, e.g. "0", "3h", "-90m", "1d", "10800" (seconds)
    HOME_TZ           IANA timezone of the home, default "Asia/Kolkata"
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_HOME_TZ = "Asia/Kolkata"
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_offset(value: str | None) -> float:
    """Parse "3h", "-90m", "1d", "45s" or plain seconds into seconds."""
    if value is None or not value.strip():
        return 0.0
    v = value.strip().lower()
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([smhd]?)", v)
    if not m:
        raise ValueError(f"invalid DEMO_TIME_OFFSET {value!r}; use e.g. '3h', '-90m', '1d' or seconds")
    return float(m.group(1)) * _UNITS.get(m.group(2) or "s", 1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DemoClock:
    def __init__(
        self,
        offset_s: float = 0.0,
        tz: str | ZoneInfo = DEFAULT_HOME_TZ,
        real_now: Callable[[], datetime] = _utc_now,
    ):
        self.tz = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
        self._real_now = real_now
        self._offset = timedelta(seconds=offset_s)
        self._lock = threading.Lock()

    @property
    def offset_s(self) -> float:
        return self._offset.total_seconds()

    def real_now(self) -> datetime:
        return self._real_now().astimezone(self.tz)

    def now(self) -> datetime:
        with self._lock:
            return (self._real_now() + self._offset).astimezone(self.tz)

    def set(self, target: datetime) -> datetime:
        """Make now() equal `target` (a naive datetime is taken as home-local time)."""
        if target.tzinfo is None:
            target = target.replace(tzinfo=self.tz)
        with self._lock:
            self._offset = target - self._real_now()
        return self.now()

    def advance(self, seconds: float) -> datetime:
        with self._lock:
            self._offset += timedelta(seconds=seconds)
        return self.now()

    def reset(self) -> datetime:
        with self._lock:
            self._offset = timedelta(0)
        return self.now()

    def as_dict(self) -> dict[str, object]:
        now = self.now()
        return {
            "sim_now": now.isoformat(timespec="seconds"),
            "real_now": self.real_now().isoformat(timespec="seconds"),
            "offset_s": round(self.offset_s, 3),
            "tz": str(self.tz),
        }


_clock: DemoClock | None = None
_clock_lock = threading.Lock()


def get_clock() -> DemoClock:
    global _clock
    with _clock_lock:
        if _clock is None:
            _clock = DemoClock(
                offset_s=parse_offset(os.getenv("DEMO_TIME_OFFSET")),
                tz=os.getenv("HOME_TZ", DEFAULT_HOME_TZ),
            )
        return _clock
