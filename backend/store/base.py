"""Doorstep state: entities and the storage interface (SQLite now, DynamoDB later)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class PackageStatus(str, Enum):
    PRESENT = "present"
    REMINDED = "reminded"
    PICKED_UP = "picked_up"
    MISSING = "missing"

    @property
    def is_open(self) -> bool:
        return self in (PackageStatus.PRESENT, PackageStatus.REMINDED)


@dataclass
class EventRecord:
    event_id: str
    event_type: str
    real_ts: str  # ISO 8601, real wall-clock time
    sim_ts: str  # ISO 8601, demo-clock time in the home timezone
    sim_hour: int
    device_id: str | None = None
    source: str = "webhook"  # webhook | simulated | demo
    frame_source: str | None = None  # ring_whep | replay | None (no capture)
    frame_count: int = 0
    package_seen: bool = False
    vehicle_seen: bool = False
    person_seen: bool = False
    description_source: str | None = None  # bedrock | stub | None
    accessible_description: str | None = None
    unusual_score: float | None = None
    unusual_explanation: str | None = None
    # What this capture could say about the open package, e.g.
    # "different view — can't verify package" (see doorstep.record_event)
    package_check: str | None = None
    # Representative frame for the UI, relative to the data dir (e.g. "frames/<event>/frame_010.jpg")
    snapshot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Package:
    id: str
    status: PackageStatus
    arrived_event_id: str
    arrived_sim_ts: str
    arrived_real_ts: str
    last_seen_sim_ts: str
    reminded_sim_ts: str | None = None
    resolved_sim_ts: str | None = None
    resolved_event_id: str | None = None
    # Camera-view fingerprint of the arrival capture (backend.vision.fingerprint);
    # only captures of the same view may mark this package missing.
    arrival_view: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Notification:
    id: str
    audience: str  # resident | caregiver
    kind: str  # package_arrived | package_reminder | package_missing | unusual_hour
    text: str
    event_id: str | None
    source: str  # where the underlying observation came from: bedrock | stub | rules
    sim_ts: str
    real_ts: str
    package_id: str | None = None
    status: str = "queued"  # queued | sent (sending comes later)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateStore(ABC):
    # events
    @abstractmethod
    def add_event(self, event: EventRecord) -> None: ...

    @abstractmethod
    def get_event(self, event_id: str) -> EventRecord | None: ...

    @abstractmethod
    def list_events(self, event_types: list[str] | None = None) -> list[EventRecord]: ...

    # packages
    @abstractmethod
    def create_package(self, package: Package) -> None: ...

    @abstractmethod
    def update_package(self, package: Package) -> None: ...

    @abstractmethod
    def get_package(self, package_id: str) -> Package | None: ...

    @abstractmethod
    def list_packages(self, statuses: list[PackageStatus] | None = None) -> list[Package]: ...

    def open_package(self) -> Package | None:
        """The current package at the door (present or reminded), if any."""
        open_ = self.list_packages([PackageStatus.PRESENT, PackageStatus.REMINDED])
        return open_[-1] if open_ else None

    # notifications
    @abstractmethod
    def add_notification(self, notification: Notification) -> None: ...

    @abstractmethod
    def list_notifications(self, audience: str | None = None) -> list[Notification]: ...

    @abstractmethod
    def clear(self) -> None:
        """Delete all events, packages and notifications (demo resets)."""
