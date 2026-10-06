from backend.store.base import EventRecord, Notification, Package, PackageStatus, StateStore
from backend.store.sqlite import SQLiteStore

__all__ = ["EventRecord", "Notification", "Package", "PackageStatus", "StateStore", "SQLiteStore", "get_store"]

_store: StateStore | None = None


def get_store() -> StateStore:
    """Process-wide store (SQLite at data/doorsight.db). A DynamoDB adapter can replace this later."""
    global _store
    if _store is None:
        from backend.config import get_settings

        _store = SQLiteStore(get_settings().data_dir / "doorsight.db")
    return _store
