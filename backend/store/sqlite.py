"""SQLite implementation of StateStore (single file, thread-safe via one lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import fields
from pathlib import Path

from backend.store.base import EventRecord, Notification, Package, PackageStatus, StateStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    real_ts TEXT NOT NULL,
    sim_ts TEXT NOT NULL,
    sim_hour INTEGER NOT NULL,
    device_id TEXT,
    source TEXT,
    frame_source TEXT,
    frame_count INTEGER NOT NULL DEFAULT 0,
    package_seen INTEGER NOT NULL DEFAULT 0,
    vehicle_seen INTEGER NOT NULL DEFAULT 0,
    person_seen INTEGER NOT NULL DEFAULT 0,
    description_source TEXT,
    accessible_description TEXT,
    unusual_score REAL,
    unusual_explanation TEXT,
    package_check TEXT,
    snapshot TEXT
);
CREATE INDEX IF NOT EXISTS events_type_hour ON events (event_type, sim_hour);

CREATE TABLE IF NOT EXISTS packages (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    arrived_event_id TEXT NOT NULL,
    arrived_sim_ts TEXT NOT NULL,
    arrived_real_ts TEXT NOT NULL,
    last_seen_sim_ts TEXT NOT NULL,
    reminded_sim_ts TEXT,
    resolved_sim_ts TEXT,
    resolved_event_id TEXT,
    arrival_view TEXT
);
CREATE INDEX IF NOT EXISTS packages_status ON packages (status);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    audience TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    event_id TEXT,
    source TEXT NOT NULL,
    sim_ts TEXT NOT NULL,
    real_ts TEXT NOT NULL,
    package_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    extra TEXT NOT NULL DEFAULT '{}'
);
"""

_BOOL_EVENT_FIELDS = {"package_seen", "vehicle_seen", "person_seen"}

# Columns added after the first release: (table, column, type). Applied to older databases on open.
MIGRATIONS = [
    ("events", "package_check", "TEXT"),
    ("packages", "arrival_view", "TEXT"),
    ("events", "snapshot", "TEXT"),
]


class SQLiteStore(StateStore):
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            for table, column, typ in MIGRATIONS:
                existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typ}")

    def _insert(self, table: str, row: dict, replace: bool = False) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        with self._lock, self._conn:
            self._conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", list(row.values()))

    def _select(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- events --------------------------------------------------------------

    def add_event(self, event: EventRecord) -> None:
        row = event.to_dict()
        for k in _BOOL_EVENT_FIELDS:
            row[k] = int(row[k])
        self._insert("events", row, replace=True)

    @staticmethod
    def _event(row: sqlite3.Row) -> EventRecord:
        d = dict(row)
        for k in _BOOL_EVENT_FIELDS:
            d[k] = bool(d[k])
        return EventRecord(**d)

    def get_event(self, event_id: str) -> EventRecord | None:
        rows = self._select("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return self._event(rows[0]) if rows else None

    def list_events(self, event_types: list[str] | None = None) -> list[EventRecord]:
        if event_types:
            marks = ", ".join("?" for _ in event_types)
            rows = self._select(f"SELECT * FROM events WHERE event_type IN ({marks}) ORDER BY sim_ts", tuple(event_types))
        else:
            rows = self._select("SELECT * FROM events ORDER BY sim_ts")
        return [self._event(r) for r in rows]

    # --- packages ------------------------------------------------------------

    @staticmethod
    def _package_row(package: Package) -> dict:
        row = package.to_dict()
        row["arrival_view"] = json.dumps(row["arrival_view"]) if row["arrival_view"] is not None else None
        return row

    def create_package(self, package: Package) -> None:
        self._insert("packages", self._package_row(package))

    def update_package(self, package: Package) -> None:
        self._insert("packages", self._package_row(package), replace=True)

    @staticmethod
    def _package(row: sqlite3.Row) -> Package:
        d = dict(row)
        d["status"] = PackageStatus(d["status"])
        d["arrival_view"] = json.loads(d["arrival_view"]) if d.get("arrival_view") else None
        return Package(**d)

    def get_package(self, package_id: str) -> Package | None:
        rows = self._select("SELECT * FROM packages WHERE id = ?", (package_id,))
        return self._package(rows[0]) if rows else None

    def list_packages(self, statuses: list[PackageStatus] | None = None) -> list[Package]:
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            rows = self._select(f"SELECT * FROM packages WHERE status IN ({marks}) ORDER BY arrived_sim_ts",
                                tuple(s.value for s in statuses))
        else:
            rows = self._select("SELECT * FROM packages ORDER BY arrived_sim_ts")
        return [self._package(r) for r in rows]

    # --- notifications -------------------------------------------------------

    def add_notification(self, notification: Notification) -> None:
        row = notification.to_dict()
        row["extra"] = json.dumps(row["extra"])
        self._insert("notifications", row)

    def list_notifications(self, audience: str | None = None) -> list[Notification]:
        if audience:
            rows = self._select("SELECT * FROM notifications WHERE audience = ? ORDER BY sim_ts, rowid", (audience,))
        else:
            rows = self._select("SELECT * FROM notifications ORDER BY sim_ts, rowid")
        out = []
        names = {f.name for f in fields(Notification)}
        for r in rows:
            d = {k: v for k, v in dict(r).items() if k in names}
            d["extra"] = json.loads(d["extra"])
            out.append(Notification(**d))
        return out

    def clear(self) -> None:
        with self._lock, self._conn:
            for table in ("events", "packages", "notifications"):
                self._conn.execute(f"DELETE FROM {table}")
