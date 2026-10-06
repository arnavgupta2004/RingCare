from backend.store.base import EventRecord, Notification, Package, PackageStatus, StateStore
from backend.store.sqlite import SQLiteStore

__all__ = ["EventRecord", "Notification", "Package", "PackageStatus", "StateStore", "SQLiteStore",
           "get_store", "store_from_env"]

_store: StateStore | None = None


def store_from_env() -> StateStore:
    """STATE_BACKEND=sqlite (default, data/doorsight.db) | dynamodb (table DYNAMODB_TABLE)."""
    import os

    from backend.config import get_settings

    settings = get_settings()  # also loads .env
    backend = os.getenv("STATE_BACKEND", "sqlite").strip().lower()
    if backend == "dynamodb":
        from backend.store.dynamodb import DynamoDBStore

        return DynamoDBStore()
    if backend != "sqlite":
        raise ValueError(f"STATE_BACKEND must be 'sqlite' or 'dynamodb', not {backend!r}")
    return SQLiteStore(settings.data_dir / "doorsight.db")


def get_store() -> StateStore:
    """Process-wide store chosen by STATE_BACKEND."""
    global _store
    if _store is None:
        _store = store_from_env()
    return _store
